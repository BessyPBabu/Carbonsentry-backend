from rest_framework.throttling import AnonRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    scope = "login"


class PasswordResetRateThrottle(AnonRateThrottle):
    scope = "password_reset"


class RegisterRateThrottle(AnonRateThrottle):
    scope = "register"


class OtpVerifyRateThrottle(AnonRateThrottle):
    scope = "otp_verify"