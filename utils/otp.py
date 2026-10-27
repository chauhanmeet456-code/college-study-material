import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import secrets
import time
from config import Config


class EmailDeliveryResult:
    """Three-state result for SMTP delivery attempts.

    ``SMTPServerDisconnected`` during the message-send phase (after the server
    accepted the ``DATA`` command but before the client reads the final ``250``
    reply) does **not** prove non-delivery — the provider may have accepted and
    queued the message, then closed the connection.  This class makes the
    distinction explicit so callers can handle the ambiguous case safely.
    """

    # String constants — never overwritten by instances.
    SUCCESS = 'success'
    FAILURE = 'failure'
    AMBIGUOUS = 'ambiguous'

    def __init__(self, status):
        self.status = status

    @property
    def is_success(self):
        return self.status == EmailDeliveryResult.SUCCESS

    @property
    def is_failure(self):
        return self.status == EmailDeliveryResult.FAILURE

    @property
    def is_ambiguous(self):
        return self.status == EmailDeliveryResult.AMBIGUOUS

    def __bool__(self):
        return self.status == EmailDeliveryResult.SUCCESS

    def __eq__(self, other):
        if isinstance(other, bool):
            return (other is True) == self.is_success
        if isinstance(other, EmailDeliveryResult):
            return self.status == other.status
        return False

    def __repr__(self):
        return f"EmailDeliveryResult({self.status})"


# Module-level singletons so callers can write ``return RESULT_SUCCESS``
# without creating a new instance each time.
RESULT_SUCCESS = EmailDeliveryResult(EmailDeliveryResult.SUCCESS)
RESULT_FAILURE = EmailDeliveryResult(EmailDeliveryResult.FAILURE)
RESULT_AMBIGUOUS = EmailDeliveryResult(EmailDeliveryResult.AMBIGUOUS)


class OTPManager:
    # Bounded retry configuration for transient SMTP failures (timeouts,
    # connection resets, server disconnects during connect/login).
    MAX_SEND_ATTEMPTS = 3
    RETRY_DELAY_SECONDS = 2

    @staticmethod
    def _mask_email_log(email):
        """Mask an email address for logging — never expose the full address."""
        if not email or '@' not in email:
            return '(unknown)'
        local, _, domain = email.partition('@')
        masked = (local[:1] + '***') if local else '***'
        return f'{masked}@{domain}'
    @staticmethod
    def generate_otp():
        return str(secrets.randbelow(900000) + 100000)

    @staticmethod
    def store_otp(email, otp, purpose='login', ip_address=None):
        from flask import session
        key = f'otp_{purpose}_{email}'
        stored = session.get(key)
        if stored:
            resend_count = stored.get('resend_count', 0)
        else:
            resend_count = 0
        session[key] = {
            'otp': otp,
            'timestamp': time.time(),
            'attempts': 0,
            'resend_count': resend_count,
            'ip_address': ip_address,
            'purpose': purpose,
        }
        session.permanent = True

    @staticmethod
    def verify_otp(email, otp, purpose='login'):
        from flask import session, flash
        key = f'otp_{purpose}_{email}'
        stored = session.get(key)

        if not stored:
            flash('OTP expired or not found. Please request a new one.', 'danger')
            return False

        if time.time() - stored['timestamp'] > Config.OTP_EXPIRY_SECONDS:
            session.pop(key, None)
            flash('OTP expired. Please request a new one.', 'danger')
            return False

        stored['attempts'] += 1
        if stored['attempts'] > Config.OTP_MAX_VERIFICATION_ATTEMPTS:
            session.pop(key, None)
            flash('Too many verification attempts. Please request a new OTP.', 'danger')
            return False

        if stored['otp'] == otp:
            session.pop(key, None)
            return True

        flash('Invalid OTP. Please try again.', 'danger')
        return False

    @staticmethod
    def is_otp_valid(email, purpose='login'):
        """Return True if a non-expired OTP challenge exists for *email*.

        Unlike ``verify_otp``, this method has **no side effects**: it does
        not increment the attempt counter, pop the session key, or show a
        flash message.  Useful for detecting duplicate submissions and
        deciding whether to show the existing OTP screen.
        """
        from flask import session
        key = f'otp_{purpose}_{email}'
        stored = session.get(key)
        if not stored:
            return False
        return time.time() - stored.get('timestamp', 0) <= Config.OTP_EXPIRY_SECONDS

    @staticmethod
    def can_resend(email, purpose='login'):
        """Check whether a resend is allowed.

        Returns (allowed, reason) tuple.  ``allowed`` is True when a new OTP
        can be sent.  ``reason`` is a user-facing string when not allowed.
        """
        from flask import session
        key = f'otp_{purpose}_{email}'
        stored = session.get(key)

        if not stored:
            return True, None

        resend_count = stored.get('resend_count', 0)
        if resend_count >= Config.OTP_MAX_RESENDS:
            return False, 'max_resends_reached'

        last_sent = stored.get('timestamp', 0)
        now = time.time()
        cooldown_remaining = Config.OTP_RESEND_COOLDOWN_SECONDS - (now - last_sent)
        if cooldown_remaining > 0:
            return False, f'cooldown:{int(cooldown_remaining)}'

        return True, None

    @staticmethod
    def increment_resend_count(email, purpose='login'):
        """Increment the resend counter after a successful email send."""
        from flask import session
        key = f'otp_{purpose}_{email}'
        stored = session.get(key)
        if stored:
            stored['resend_count'] = stored.get('resend_count', 0) + 1
            stored['timestamp'] = time.time()
            session[key] = stored

    @staticmethod
    def get_resend_remaining(email, purpose='login'):
        """Return the number of resends still available (0 to OTP_MAX_RESENDS)."""
        from flask import session
        key = f'otp_{purpose}_{email}'
        stored = session.get(key)
        if not stored:
            return Config.OTP_MAX_RESENDS
        return max(0, Config.OTP_MAX_RESENDS - stored.get('resend_count', 0))

    @staticmethod
    def _attempt_send(to_email, subject, body):
        """Perform a single SMTP connection + send attempt.

        Returns ``(result, can_retry, phase)`` where *can_retry* is True when
        the failure was transient and a second attempt is meaningful (i.e. the
        disconnect happened before the message payload was sent).
        """
        import socket as _socket

        start = time.time()
        server = None
        phase = 'init'
        data_sent = False

        # Build the MIME message outside the retry loop — it is safe to reuse.
        msg = MIMEMultipart()
        msg['From'] = Config.MAIL_USERNAME
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html'))

        try:
            context = ssl.create_default_context()
            connect_timeout = getattr(Config, 'MAIL_CONNECT_TIMEOUT', 5)
            old_default = _socket.getdefaulttimeout()
            _socket.setdefaulttimeout(connect_timeout)
            try:
                phase = 'connect'
                server = smtplib.SMTP(Config.MAIL_SERVER, Config.MAIL_PORT,
                                      timeout=connect_timeout)
            finally:
                _socket.setdefaulttimeout(old_default)

            # After the initial handshake, use the longer MAIL_TIMEOUT for the
            # remaining SMTP conversation (STARTTLS, login, send).
            if server and server.sock:
                try:
                    server.sock.settimeout(Config.MAIL_TIMEOUT)
                except (AttributeError, OSError):
                    pass

            phase = 'starttls'
            server.starttls(context=context)

            phase = 'login'
            server.login(Config.MAIL_USERNAME, Config.MAIL_PASSWORD)

            # Use a shorter timeout for the message-send phase so that a
            # silently-disconnecting server is detected sooner, reducing the
            # worst-case login delay.
            send_timeout = getattr(Config, 'MAIL_SEND_TIMEOUT', 8)
            if server and server.sock:
                try:
                    server.sock.settimeout(min(Config.MAIL_TIMEOUT, send_timeout))
                except (AttributeError, OSError):
                    pass

            phase = 'send'
            server.send_message(msg)
            data_sent = True

            elapsed = time.time() - start
            print(f'[TIMING] Email sent in {elapsed:.3f}s', flush=True)
            return RESULT_SUCCESS, False, phase
        except smtplib.SMTPServerDisconnected as e:
            elapsed = time.time() - start
            if data_sent:
                # send_message() completed; the disconnect happened during
                # quit() or afterwards — the email was sent.
                print(f'[TIMING] Email sent but server disconnected after {elapsed:.3f}s',
                      flush=True)
                return RESULT_SUCCESS, False, phase
            if phase == 'send':
                # The server may have accepted the message data before
                # disconnecting.  This is common with Gmail and some other
                # providers that accept the payload and close the connection
                # before the client reads the final 250 reply.
                print(f'[TIMING] Email send interrupted after {elapsed:.3f}s: '
                      f'SMTPServerDisconnected (ambiguous — message data likely sent)',
                      flush=True)
                # Do NOT retry — the message may already have been sent,
                # retrying risks duplicate delivery.
                return RESULT_AMBIGUOUS, False, phase
            # Disconnect during connect / starttls / login — transient
            print(f'[TIMING] Email failed after {elapsed:.3f}s: '
                  f'SMTPServerDisconnected (pre-send, phase={phase})',
                  flush=True)
            return RESULT_FAILURE, True, phase
        except smtplib.SMTPAuthenticationError:
            elapsed = time.time() - start
            # Authentication failure is permanent — never retry.
            print(f'[TIMING] SMTP authentication failed after {elapsed:.3f}s',
                  flush=True)
            return RESULT_FAILURE, False, phase
        except (TimeoutError, ConnectionError) as e:
            elapsed = time.time() - start
            if phase == 'send':
                # Timeout during message send is ambiguous — data may have been
                # transmitted.  Do not retry to avoid duplicates.
                print(f'[TIMING] Email send interrupted after {elapsed:.3f}s: '
                      f'Timeout (ambiguous — message data may have been sent)',
                      flush=True)
                return RESULT_AMBIGUOUS, False, phase
            # Timeout or connection error during connect/starttls/login — retry
            print(f'[TIMING] Email failed after {elapsed:.3f}s: '
                  f'{type(e).__name__} (phase={phase})',
                  flush=True)
            return RESULT_FAILURE, True, phase
        except OSError as e:
            # Catch-all OSError (socket-level) that is not TimeoutError or
            # ConnectionError — e.g. network unreachable.  These are typically
            # transient during connect/login.
            elapsed = time.time() - start
            if phase == 'send':
                # OSError during send is ambiguous — data may have been sent.
                print(f'[TIMING] Email send interrupted after {elapsed:.3f}s: '
                      f'OSError (ambiguous — message data may have been sent)',
                      flush=True)
                return RESULT_AMBIGUOUS, False, phase
            print(f'[TIMING] Email failed after {elapsed:.3f}s: '
                  f'{type(e).__name__} (phase={phase})',
                  flush=True)
            return RESULT_FAILURE, True, phase
        except Exception as e:
            elapsed = time.time() - start
            if data_sent:
                return RESULT_SUCCESS, False, phase
            if phase == 'send':
                # Unknown error during send — treat as ambiguous, do not retry.
                print(f'[TIMING] Email send interrupted after {elapsed:.3f}s: '
                      f'{type(e).__name__} (ambiguous)',
                      flush=True)
                return RESULT_AMBIGUOUS, False, phase
            print(f'[TIMING] Email failed after {elapsed:.3f}s: {type(e).__name__}',
                  flush=True)
            return RESULT_FAILURE, True, phase
        finally:
            if server:
                try:
                    # Only attempt QUIT if we still have a live connection.
                    # Calling quit() on an already-disconnected socket can
                    # trigger an additional timeout that adds to the delay.
                    if not data_sent:
                        # Use a short timeout for the QUIT handshake so that
                        # a dead socket doesn't cause a multi-second hang.
                        if hasattr(server, 'sock') and server.sock:
                            try:
                                server.sock.settimeout(
                                    getattr(Config, 'MAIL_QUIT_TIMEOUT', 2)
                                )
                            except (AttributeError, OSError):
                                pass
                        server.quit()
                except Exception:
                    pass

    @staticmethod
    def send_email(to_email, subject, body):
        """Send an email via SMTP and return an ``EmailDeliveryResult``.

        * **SUCCESS** – the SMTP server accepted the message (``250`` reply).
        * **AMBIGUOUS** – the server accepted the ``DATA`` payload or went
          silent while the client was sending it (``SMTPServerDisconnected``
          or timeout raised *during* ``send_message``).  The message was
          transmitted to the server and may have been delivered.
        * **FAILURE** – anything else (connection refused, TLS error,
          authentication failure, server disconnect before ``DATA``).

        Returning ``EmailDeliveryResult`` (instead of ``bool``) lets callers
        distinguish the ambiguous case and avoid clearing a pending OTP or
        incrementing a resend counter when the email may already have been
        sent.

        Transient failures (connection timeout, server disconnect before
        ``DATA``) are retried up to ``MAX_SEND_ATTEMPTS`` times with a short
        delay, so brief network hiccups don't permanently block OTP delivery.
        """
        import logging
        smtp_logger = logging.getLogger('studyhub.smtp')

        max_attempts = OTPManager.MAX_SEND_ATTEMPTS
        retry_delay = OTPManager.RETRY_DELAY_SECONDS

        for attempt in range(1, max_attempts + 1):
            try:
                result, can_retry, phase = OTPManager._attempt_send(
                    to_email, subject, body,
                )
            except Exception as exc:
                # Safety net: _attempt_send has its own exception handling for
                # known SMTP errors, but if an unexpected exception escapes
                # (e.g. from the finally block's quit()), treat it as a
                # transient failure and retry rather than crashing.
                smtp_logger.warning(
                    'Unexpected exception in _attempt_send: %s',
                    type(exc).__name__,
                )
                if attempt >= max_attempts:
                    return RESULT_FAILURE
                time.sleep(retry_delay)
                continue

            if result.is_success or not can_retry or attempt >= max_attempts:
                return result

            masked = OTPManager._mask_email_log(to_email)
            smtp_logger.warning(
                'SMTP send attempt %d/%d failed for %s (phase=%s), '
                'retrying in %ds',
                attempt, max_attempts, masked, phase, retry_delay,
            )
            time.sleep(retry_delay)

        return RESULT_FAILURE

    @staticmethod
    def is_configured():
        """Return True when SMTP credentials are present."""
        return bool(Config.MAIL_USERNAME and Config.MAIL_PASSWORD)

    @staticmethod
    def send_otp_email(to_email, otp, purpose='login'):
        subject_map = {
            'login': 'Your StudyHub Login OTP',
            'register': 'Your StudyHub Registration OTP',
            'forgot': 'Your StudyHub Password Reset OTP',
            'delete_account': 'Confirm Your Account Deletion - StudyHub'
        }

        subject = subject_map.get(purpose, 'Your StudyHub OTP')

        if purpose == 'delete_account':
            body = f"""
            <html>
                <body style="margin: 0; padding: 0; background-color: #fef2f2; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;">
                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #fef2f2; padding: 40px 0;">
                        <tr>
                            <td align="center">
                                <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="max-width: 600px; width: 100%;">
                                    <tr>
                                        <td style="background: linear-gradient(135deg, #dc2626 0%, #b91c1c 100%); padding: 40px 30px; border-radius: 12px 12px 0 0; text-align: center;">
                                            <h1 style="margin: 0; font-size: 32px; font-weight: 700; color: #ffffff; letter-spacing: -0.5px;">StudyHub</h1>
                                            <p style="margin: 8px 0 0; font-size: 14px; color: rgba(255,255,255,0.9);">Account Deletion Confirmation</p>
                                        </td>
                                    </tr>
                                    <tr>
                                        <td style="background-color: #ffffff; padding: 40px 30px; border-radius: 0 0 12px 12px; box-shadow: 0 4px 20px rgba(220, 38, 38, 0.08);">
                                            <h2 style="margin: 0 0 8px; font-size: 22px; font-weight: 600; color: #dc2626;">Delete Account Confirmation</h2>
                                            <p style="margin: 0 0 28px; font-size: 15px; color: #64748b; line-height: 1.6;">
                                                We received a request to permanently delete your StudyHub account. Use the verification code below to confirm this action.
                                            </p>
                                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                                                <tr>
                                                    <td align="center" style="padding: 24px 0;">
                                                        <div style="display: inline-block; background-color: #fef2f2; border: 2px dashed #dc2626; border-radius: 12px; padding: 24px 40px; min-width: 260px;">
                                                            <span style="font-size: 36px; font-weight: 700; color: #dc2626; letter-spacing: 8px;">{otp}</span>
                                                        </div>
                                                    </td>
                                                </tr>
                                            </table>
                                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top: 28px;">
                                                <tr>
                                                    <td style="background-color: #fef3c7; border-left: 4px solid #f59e0b; padding: 14px 18px; border-radius: 6px;">
                                                        <p style="margin: 0; font-size: 14px; color: #92400e; line-height: 1.5;">
                                                            <strong>Warning:</strong> This code expires in 3 minutes. After verification, your account will be permanently deleted and cannot be recovered.
                                                        </p>
                                                    </td>
                                                </tr>
                                            </table>
                                            <p style="margin: 24px 0 0; font-size: 14px; color: #64748b; line-height: 1.6;">
                                                If you did not request account deletion, please ignore this email and your account will remain safe.
                                            </p>
                                        </td>
                                    </tr>
                                    <tr>
                                        <td style="padding: 24px 30px 0; text-align: center;">
                                            <p style="margin: 0; font-size: 13px; color: #94a3b8;">
                                                &copy; {Config.APP_NAME} - College Study Material Platform
                                            </p>
                                        </td>
                                    </tr>
                                </table>
                            </td>
                        </tr>
                    </table>
                </body>
            </html>
            """
        else:
            body = f"""
            <html>
                <body style="margin: 0; padding: 0; background-color: #f4f6f8; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;">
                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f4f6f8; padding: 40px 0;">
                        <tr>
                            <td align="center">
                                <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="max-width: 600px; width: 100%;">
                                    <tr>
                                        <td style="background: linear-gradient(135deg, #2563eb 0%, #7c3aed 100%); padding: 40px 30px; border-radius: 12px 12px 0 0; text-align: center;">
                                            <h1 style="margin: 0; font-size: 32px; font-weight: 700; color: #ffffff; letter-tracking: -0.5px;">StudyHub</h1>
                                            <p style="margin: 8px 0 0; font-size: 14px; color: rgba(255,255,255,0.85);">College Study Material Platform</p>
                                        </td>
                                    </tr>
                                    <tr>
                                        <td style="background-color: #ffffff; padding: 40px 30px; border-radius: 0 0 12px 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.06);">
                                            <h2 style="margin: 0 0 8px; font-size: 22px; font-weight: 600; color: #1e293b;">Verification Code</h2>
                                            <p style="margin: 0 0 28px; font-size: 15px; color: #64748b; line-height: 1.6;">
                                                Use the verification code below to complete your {purpose}. This code helps us keep your account secure.
                                            </p>
                                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                                                <tr>
                                                    <td align="center" style="padding: 24px 0;">
                                                        <div style="display: inline-block; background-color: #f8favc; border: 2px dashed #2563eb; border-radius: 12px; padding: 24px 40px; min-width: 260px;">
                                                            <span style="font-size: 36px; font-weight: 700; color: #2563eb; letter-spacing: 8px;">{otp}</span>
                                                        </div>
                                                    </td>
                                                </tr>
                                            </table>
                                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top: 28px;">
                                                <tr>
                                                    <td style="background-color: #fef3c7; border-left: 4px solid #f59e0b; padding: 14px 18px; border-radius: 6px;">
                                                        <p style="margin: 0; font-size: 14px; color: #92400e; line-height: 1.5;">
                                                            <strong>Expires in 5 minutes.</strong> Please enter this code before it expires.
                                                        </p>
                                                    </td>
                                                </tr>
                                            </table>
                                            <p style="margin: 24px 0 0; font-size: 14px; color: #64748b; line-height: 1.6;">
                                                If you did not request this code, you can safely ignore this email. Your account will remain secure.
                                            </p>
                                        </td>
                                    </tr>
                                    <tr>
                                        <td style="padding: 24px 30px 0; text-align: center;">
                                            <p style="margin: 0; font-size: 13px; color: #94a3b8;">
                                                &copy; {Config.APP_NAME} - College Study Material Platform
                                            </p>
                                        </td>
                                    </tr>
                                </table>
                            </td>
                        </tr>
                    </table>
                </body>
            </html>
            """

        return OTPManager.send_email(to_email, subject, body)

    @staticmethod
    def send_notification_email(to_email, subject, body):
        """Send a plain notification email (not an OTP).

        Reuses the same SMTP infrastructure as OTP emails.
        Returns an ``EmailDeliveryResult``.  Callers that only need a
        success/failure boolean can use ``bool(result)``.
        """
        if not OTPManager.is_configured():
            return RESULT_FAILURE
        return OTPManager.send_email(to_email, subject, body)
