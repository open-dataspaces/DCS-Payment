"""Custom Validators"""
import re
from typing import Any


def validate_email(email: str) -> bool:
    """Validate email format"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    return bool(re.match(pattern, email))


def validate_password_strength(password: str) -> bool:
    """
    Validate password strength
    - At least 8 characters
    - Contains uppercase and lowercase
    - Contains number
    """
    if len(password) < 8:
        return False
    
    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    
    return has_upper and has_lower and has_digit


def sanitize_string(text: str) -> str:
    """Sanitize string input"""
    # Remove potential XSS patterns
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<.*?>', '', text)
    return text.strip()