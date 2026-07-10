import boto3
from botocore.exceptions import ClientError
from app.utils.logger import logger
from app.config.config import config

AWS_REGION = config.get("AWS_REGION", "us-east-1")
SENDER_EMAIL_ADDRESS = config.get("SENDER_EMAIL_ADDRESS")

async def send_password_reset_email(recipient_email: str, reset_link: str):
    """
    Sends a password reset email to the user.
    This is a basic implementation. You'll need to configure AWS credentials
    and SES verified sender identity for this to work.
    """
    if not SENDER_EMAIL_ADDRESS:
        logger.error("SENDER_EMAIL_ADDRESS is not configured. Cannot send password reset email.")
        # In a real scenario, you might raise an error or handle this more gracefully
        return False

    subject = "Reset Your Aiditor Password"
    
    body_html = f"""
    <html>
    <head></head>
    <body>
      <h1>Password Reset Request</h1>
      <p>Hi,</p>
      <p>You recently requested to reset your password for your Aiditor account. Click the link below to reset it:</p>
      <p><a href=\"{reset_link}\">Reset Password</a></p>
      <p>If you did not request a password reset, please ignore this email or contact support if you have concerns.</p>
      <p>This link will expire in 1 hour.</p>
      <p>Thanks,<br>The Aiditor Team</p>
    </body>
    </html>
    """
    
    body_text = f"""
    Hi,
    
    You recently requested to reset your password for your Aiditor account. Copy and paste the following link into your browser to reset it:
    {reset_link}
    
    If you did not request a password reset, please ignore this email or contact support if you have concerns.
    This link will expire in 1 hour.
    
    Thanks,
    The Aiditor Team
    """

    client = boto3.client("ses", region_name=AWS_REGION)

    try:
        response = client.send_email(
            Destination={
                "ToAddresses": [
                    recipient_email,
                ],
            },
            Message={
                "Body": {
                    "Html": {
                        "Charset": "UTF-8",
                        "Data": body_html,
                    },
                    "Text": {
                        "Charset": "UTF-8",
                        "Data": body_text,
                    },
                },
                "Subject": {
                    "Charset": "UTF-8",
                    "Data": subject,
                },
            },
            Source=SENDER_EMAIL_ADDRESS,
        )
    except ClientError as e:
        logger.error(f"Email sending failed: {e.response['Error']['Message']}")
        return False
    except Exception as e:
        logger.error(f"An unexpected error occurred while sending email: {str(e)}")
        return False
    else:
        logger.info(f"Password reset email sent successfully to {recipient_email}. Message ID: {response['MessageId']}")
        return True