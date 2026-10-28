class BridgeError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def disabled(message="Hashy Bridge is disabled."):
    return BridgeError("bridge_disabled", message, 503)


def unauthorized(message="Invalid bearer token."):
    return BridgeError("unauthorized", message, 401)


def forbidden(message="The token user cannot access this resource."):
    return BridgeError("forbidden", message, 403)


def invalid_request(message):
    return BridgeError("invalid_request", message, 400)
