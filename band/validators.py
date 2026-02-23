import re
from django.core.exceptions import ValidationError

# Safe special characters — excludes ' " ` \ and space which can cause
# issues in shell/config contexts or ambiguous escaping.
ALLOWED_SPECIAL = r'!@#$%^&*()\-_=+\[\]{}|;:,.<>?/~'
ALLOWED_SPECIAL_DISPLAY = '! @ # $ % ^ & * ( ) - _ = + [ ] { } | ; : , . < > ? / ~'


class StrongPasswordValidator:
    def validate(self, password, user=None):
        errors = []
        if len(password) < 8:
            errors.append('at least 8 characters')
        if not re.search(r'[A-Z]', password):
            errors.append('at least one uppercase letter')
        if not re.search(r'[0-9]', password):
            errors.append('at least one number')
        if not re.search(rf'[{ALLOWED_SPECIAL}]', password):
            errors.append(f'at least one special character ({ALLOWED_SPECIAL_DISPLAY})')
        if errors:
            raise ValidationError(
                'Password must contain: ' + ', '.join(errors) + '.',
                code='password_too_weak',
            )

    def get_help_text(self):
        return (
            f'Your password must be at least 8 characters and contain at least one '
            f'uppercase letter, one number, and one special character '
            f'({ALLOWED_SPECIAL_DISPLAY}).'
        )
