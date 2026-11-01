import json


def generate_message(message_type, content):
    return json.dumps({"type": message_type, "message": content})
