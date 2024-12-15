from flask import request, jsonify
from functools import wraps
from config import API_TOKEN
def require_token(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({"error": "Authentication required."}), 401
        
        if token != f"Bearer {API_TOKEN}":
            return jsonify({"error": "Invalid token."}), 401
            
        return f(*args, **kwargs)
    return decorated