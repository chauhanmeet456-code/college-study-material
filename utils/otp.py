import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import secrets
import time
from config import Config

class OTPManager:
    @staticmethod
    def generate_otp():
        return str(secrets.randbelow(900000) + 100000)
    
    @staticmethod
    def store_otp(email, otp, purpose='login'):
        from flask import session
        key = f'otp_{purpose}_{email}'
        session[key] = {
            'otp': otp,
            'timestamp': time.time(),
            'attempts': 0
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
        
        if time.time() - stored['timestamp'] > 180:
            session.pop(key, None)
            flash('OTP expired. Please request a new one.', 'danger')
            return False
        
        stored['attempts'] += 1
        if stored['attempts'] > 3:
            session.pop(key, None)
            flash('Too many attempts. Please request a new OTP.', 'danger')
            return False
        
        if stored['otp'] == otp:
            session.pop(key, None)
            return True
        
        flash('Invalid OTP. Please try again.', 'danger')
        return False
    
    @staticmethod
    def send_email(to_email, subject, body):
        try:
            msg = MIMEMultipart()
            msg['From'] = Config.MAIL_USERNAME
            msg['To'] = to_email
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'html'))

            context = ssl.create_default_context()
            # A bounded timeout keeps a dead mail server from hanging a
            # request; a failed send is reported, not silently retried.
            with smtplib.SMTP(Config.MAIL_SERVER, Config.MAIL_PORT,
                              timeout=Config.MAIL_TIMEOUT) as server:
                server.starttls(context=context)
                server.login(Config.MAIL_USERNAME, Config.MAIL_PASSWORD)
                server.send_message(msg)
            return True
        except Exception as e:
            print(f"Email error: {e}")
            return False
    
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
                                                    <td style="background-color: #fef2f2; border-left: 4px solid #dc2626; padding: 14px 18px; border-radius: 6px;">
                                                        <p style="margin: 0; font-size: 14px; color: #991b1b; line-height: 1.5;">
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
                                            <h1 style="margin: 0; font-size: 32px; font-weight: 700; color: #ffffff; letter-spacing: -0.5px;">StudyHub</h1>
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
                                                        <div style="display: inline-block; background-color: #f8fafc; border: 2px dashed #2563eb; border-radius: 12px; padding: 24px 40px; min-width: 260px;">
                                                            <span style="font-size: 36px; font-weight: 700; color: #2563eb; letter-spacing: 8px;">{otp}</span>
                                                        </div>
                                                    </td>
                                                </tr>
                                            </table>
                                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top: 28px;">
                                                <tr>
                                                    <td style="background-color: #fef3c7; border-left: 4px solid #f59e0b; padding: 14px 18px; border-radius: 6px;">
                                                        <p style="margin: 0; font-size: 14px; color: #92400e; line-height: 1.5;">
                                                            <strong>Expires in 3 minutes.</strong> Please enter this code before it expires.
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
