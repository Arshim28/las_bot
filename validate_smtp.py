#!/usr/bin/env python3
"""
SMTP Validation Script for Stock Watchdog
Tests the Hostinger SMTP configuration
"""

import asyncio
import json
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

def load_config():
    """Load configuration from config.json"""
    try:
        with open('config.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print("ERROR: config.json not found")
        return None
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in config.json: {e}")
        return None

def test_smtp_connection():
    """Test SMTP connection to Hostinger"""
    config = load_config()
    if not config:
        return False
    
    email_config = config.get('email_sender', {})
    
    # Get password from environment or config
    password = os.getenv('SMTP_PASSWORD') or email_config.get('smtp_password', '')
    
    if not password:
        print("ERROR: SMTP password not found in environment variable SMTP_PASSWORD or config.json")
        return False
    
    smtp_host = email_config.get('smtp_host', 'smtp.hostinger.com')
    smtp_port = email_config.get('smtp_port', 587)
    smtp_username = email_config.get('smtp_username', email_config.get('from_email', ''))
    
    print(f"Testing SMTP connection to {smtp_host}:{smtp_port}")
    print(f"Username: {smtp_username}")
    
    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.set_debuglevel(1)  # Enable debug output
            server.starttls()
            server.login(smtp_username, password)
            print("✅ SMTP connection successful!")
            return True
    except Exception as e:
        print(f"❌ SMTP connection failed: {e}")
        return False

def test_send_email():
    """Test sending a simple email"""
    config = load_config()
    if not config:
        return False
    
    email_config = config.get('email_sender', {})
    recipients = config.get('recipients', [])
    
    if not recipients:
        print("ERROR: No recipients configured")
        return False
    
    password = os.getenv('SMTP_PASSWORD') or email_config.get('smtp_password', '')
    if not password:
        print("ERROR: SMTP password not configured")
        return False
    
    # Create test email
    msg = MIMEMultipart('alternative')
    msg['Subject'] = "Stock Watchdog SMTP Test"
    msg['From'] = f"{email_config.get('from_name', 'Stock Watchdog')} <{email_config.get('from_email', '')}>"
    msg['To'] = recipients[0]['email']
    
    html_content = """
    <html>
        <body>
            <h2>Stock Watchdog SMTP Test</h2>
            <p>This is a test email to verify that the Hostinger SMTP configuration is working correctly.</p>
            <p>If you receive this email, the configuration is successful!</p>
            <hr>
            <p><em>Stock Watchdog Service</em></p>
        </body>
    </html>
    """
    
    html_part = MIMEText(html_content, 'html')
    msg.attach(html_part)
    
    try:
        with smtplib.SMTP(email_config.get('smtp_host', 'smtp.hostinger.com'), 
                         email_config.get('smtp_port', 587)) as server:
            server.starttls()
            server.login(email_config.get('smtp_username', email_config.get('from_email', '')), password)
            server.send_message(msg)
            print(f"✅ Test email sent successfully to {recipients[0]['email']}")
            return True
    except Exception as e:
        print(f"❌ Failed to send test email: {e}")
        return False

def main():
    """Main validation function"""
    print("Stock Watchdog SMTP Validation")
    print("=" * 40)
    
    # Test 1: SMTP Connection
    print("\n1. Testing SMTP Connection...")
    connection_ok = test_smtp_connection()
    
    if not connection_ok:
        print("\n❌ SMTP connection failed. Please check your configuration.")
        return False
    
    # Test 2: Send Test Email
    print("\n2. Testing Email Sending...")
    email_ok = test_send_email()
    
    if email_ok:
        print("\n✅ All tests passed! Your Hostinger SMTP configuration is working correctly.")
        return True
    else:
        print("\n❌ Email sending failed. Check the error messages above.")
        return False

if __name__ == "__main__":
    try:
        success = main()
        exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nValidation interrupted by user")
        exit(1)
    except Exception as e:
        print(f"\nValidation crashed: {e}")
        exit(1) 