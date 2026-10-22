# Email Configuration Guide - StudyHub

## Overview
StudyHub uses OTP (One-Time Password) authentication for secure login and password reset. OTPs are sent via email using SMTP.

## Supported Email Providers

### 1. Gmail (Recommended for Testing)

#### Setup Steps:
1. Enable 2-Factor Authentication on your Google account
2. Generate an App Password:
   - Go to Google Account → Security → 2-Step Verification → App passwords
   - Select "Mail" and "Other (custom name)"
   - Copy the 16-character app password
3. Update `.env` file:

```env
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=your_email@gmail.com
MAIL_PASSWORD=your_16_digit_app_password
```

#### Important Notes:
- Do NOT use your regular Gmail password
- App Password is required when 2FA is enabled
- Without 2FA, Gmail blocks SMTP access

### 2. Outlook/Hotmail

```env
MAIL_SERVER=smtp.office365.com
MAIL_PORT=587
MAIL_USERNAME=your_email@outlook.com
MAIL_PASSWORD=your_password
```

### 3. Yahoo Mail

```env
MAIL_SERVER=smtp.mail.yahoo.com
MAIL_PORT=587
MAIL_USERNAME=your_email@yahoo.com
MAIL_PASSWORD=your_app_password
```

### 4. Custom SMTP Server

```env
MAIL_SERVER=your_smtp_server.com
MAIL_PORT=587
MAIL_USERNAME=your_username
MAIL_PASSWORD=your_password
```

## Port Options

| Port | Use Case | TLS/SSL |
|------|----------|---------|
| 587 | Most providers | STARTTLS |
| 465 | Alternative | SSL/TLS |
| 25 | Legacy/Internal | None |

## Current `.env` Configuration

```env
# Database
DB_HOST=localhost
DB_PORT=3306
DB_NAME=college_study
DB_USER=root
DB_PASSWORD=your_password

# Application
SECRET_KEY=your_secret_key_here_change_in_production
USE_SQLITE=true

# Email Configuration
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=your_email@gmail.com
MAIL_PASSWORD=your_app_password
APP_NAME=StudyHub
```

## Testing Without Email

If email is not configured, the system will:
1. Generate OTP successfully
2. Print OTP to console/terminal
3. Return success response
4. OTP will be visible in server logs

This allows testing without SMTP setup.

## Troubleshooting

### Error: "535-5.7.8 Username and Password not accepted"
- **Cause**: Invalid Gmail credentials
- **Solution**: Use App Password, not regular password

### Error: "Connection refused"
- **Cause**: Wrong SMTP server or port
- **Solution**: Verify server settings with your email provider

### Error: "Timeout"
- **Cause**: Firewall blocking SMTP port
- **Solution**: Check network/firewall settings

### OTP Not Received
- Check spam/junk folder
- Verify email address is correct
- Check SMTP logs in terminal
- Ensure MAIL_USERNAME and MAIL_PASSWORD are set

## Security Best Practices

1. **Never commit `.env` file** - Add to `.gitignore`
2. **Use App Passwords** - Never use main account password
3. **Rotate credentials regularly** - Change passwords periodically
4. **Use environment variables** - Don't hardcode in code
5. **Enable 2FA** - Always use 2-factor authentication

## Production Deployment

For production, consider:
- **SendGrid** (recommended for production)
- **Mailgun**
- **Amazon SES**
- **Postmark**

Example for SendGrid:
```env
MAIL_SERVER=smtp.sendgrid.net
MAIL_PORT=587
MAIL_USERNAME=apikey
MAIL_PASSWORD=your_sendgrid_api_key
```

## Support

If you face issues:
1. Check terminal logs for detailed error messages
2. Verify SMTP credentials are correct
3. Test with a simple SMTP client first
4. Check firewall/antivirus settings
