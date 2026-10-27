"""Shared pytest fixtures and pre-test setup.

This conftest is loaded *before* any test module, which lets us capture the
original ``OTPManager`` methods before ``tests_project.py`` replaces them
with always-success stubs at import time.
"""

from utils.otp import OTPManager

# Capture originals before any test module overrides them.
SAVED_SEND_EMAIL = OTPManager.send_email
SAVED_SEND_OTP_EMAIL = OTPManager.send_otp_email
SAVED_IS_CONFIGURED = OTPManager.is_configured
SAVED_MAX_SEND_ATTEMPTS = OTPManager.MAX_SEND_ATTEMPTS
SAVED_RETRY_DELAY_SECONDS = OTPManager.RETRY_DELAY_SECONDS
