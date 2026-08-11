from werkzeug.security import check_password_hash, generate_password_hash


def hash_password(plaintext):
    return generate_password_hash(plaintext)


def verify_password(plaintext, password_hash):
    return check_password_hash(password_hash, plaintext)
