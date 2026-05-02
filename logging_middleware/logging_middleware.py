import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

items_db: dict = {}
next_id: int = 1

url = "http://20.207.122.201/evaluation-service/logs"

stacks = {"backend", "frontend"}
levels = {"debug", "info", "warn", "error", "fatal"}
backend_packages = {
    "cache", "controller", "cron_job", "db", "domain",
    "handler", "repository", "route", "service"
}
shared_packages = {"auth", "config", "middleware", "utils"}
packages = backend_packages | shared_packages


def Log(stack: str, level: str, package: str, message: str) -> dict:
    if stack not in stacks:
        raise ValueError(f"Invalid stack '{stack}'. Must be one of {stacks}")
    if level not in levels:
        raise ValueError(f"Invalid level '{level}'. Must be one of {levels}")
    if package not in packages:
        raise ValueError(f"Invalid package '{package}'. Must be one of {packages}")

    payload = {
        "stack": stack,
        "level": level,
        "package": package,
        "message": message
    }

    try:
        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError:
        print(f"[LOG ERROR] Could not connect to log server. Payload was: {payload}")
        return {"error": "Log server unreachable"}
    except requests.exceptions.Timeout:
        print(f"[LOG ERROR] Log server request timed out. Payload was: {payload}")
        return {"error": "Log server timeout"}
    except requests.exceptions.HTTPError as e:
        print(f"[LOG ERROR] HTTP error from log server: {e}. Payload was: {payload}")
        return {"error": str(e)}


@app.before_request
def log_incoming_request():
    Log(
        "backend", "info", "middleware",
        f"Incoming {request.method} request to {request.path} from {request.remote_addr}"
    )


@app.after_request
def log_outgoing_response(response):
    Log(
        "backend", "info", "middleware",
        f"Response {response.status_code} sent for {request.method} {request.path}"
    )
    return response


@app.errorhandler(404)
def not_found(e):
    Log("backend", "warn", "route", f"404 Not Found: {request.method} {request.path}")
    return jsonify({"error": "Resource not found"}), 404


@app.errorhandler(405)
def method_not_allowed(e):
    Log("backend", "warn", "route", f"405 Method Not Allowed: {request.method} {request.path}")
    return jsonify({"error": "Method not allowed"}), 405


@app.errorhandler(500)
def internal_error(e):
    Log("backend", "fatal", "handler", f"500 Internal Server Error on {request.path}: {str(e)}")
    return jsonify({"error": "Internal server error"}), 500


@app.route("/health", methods=["GET"])
def health_check():
    Log("backend", "debug", "route", "Health check endpoint called - application is running")
    return jsonify({"status": "ok"}), 200


@app.route("/items", methods=["GET"])
def get_items():
    Log("backend", "debug", "service", f"Fetching all items - current count: {len(items_db)}")

    if not items_db:
        Log("backend", "info", "service", "No items found in the database, returning empty list")

    return jsonify({"items": list(items_db.values()), "count": len(items_db)}), 200


@app.route("/items/<int:item_id>", methods=["GET"])
def get_item(item_id):
    Log("backend", "debug", "service", f"Fetching item with id={item_id}")

    item = items_db.get(item_id)
    if not item:
        Log("backend", "warn", "service", f"Item with id={item_id} not found in database")
        return jsonify({"error": f"Item {item_id} not found"}), 404

    Log("backend", "info", "service", f"Successfully retrieved item id={item_id}, name='{item['name']}'")
    return jsonify(item), 200


@app.route("/items", methods=["POST"])
def create_item():
    global next_id

    Log("backend", "debug", "handler", "Create item request received, parsing request body")

    data = request.get_json()

    if not data:
        Log("backend", "error", "handler", "Create item failed - request body is missing or not valid JSON")
        return jsonify({"error": "Request body must be JSON"}), 400

    if "name" not in data:
        Log("backend", "error", "handler", f"Create item failed - 'name' field missing from payload: {data}")
        return jsonify({"error": "'name' field is required"}), 400

    if not isinstance(data["name"], str):
        Log("backend", "error", "handler", f"Create item failed - 'name' must be a string, got {type(data['name']).__name__}")
        return jsonify({"error": "'name' must be a string"}), 400

    item = {
        "id": next_id,
        "name": data["name"],
        "description": data.get("description", "")
    }

    Log("backend", "debug", "service", f"Persisting new item to database: id={next_id}, name='{item['name']}'")

    items_db[next_id] = item
    next_id += 1

    Log("backend", "info", "service", f"Item created successfully with id={item['id']}, name='{item['name']}'")

    return jsonify(item), 201


@app.route("/items/<int:item_id>", methods=["PUT"])
def update_item(item_id):
    Log("backend", "debug", "handler", f"Update request received for item id={item_id}")

    if item_id not in items_db:
        Log("backend", "warn", "service", f"Update failed - item id={item_id} does not exist in database")
        return jsonify({"error": f"Item {item_id} not found"}), 404

    data = request.get_json()
    if not data:
        Log("backend", "error", "handler", f"Update item id={item_id} failed - request body is missing or not valid JSON")
        return jsonify({"error": "Request body must be JSON"}), 400

    old_item = dict(items_db[item_id])
    items_db[item_id].update({
        "name": data.get("name", items_db[item_id]["name"]),
        "description": data.get("description", items_db[item_id]["description"])
    })

    Log("backend", "info", "service", f"Item id={item_id} updated. Changes: name '{old_item['name']}' -> '{items_db[item_id]['name']}'")

    return jsonify(items_db[item_id]), 200


@app.route("/items/<int:item_id>", methods=["DELETE"])
def delete_item(item_id):
    Log("backend", "debug", "service", f"Delete request received for item id={item_id}")

    if item_id not in items_db:
        Log("backend", "warn", "service", f"Delete failed - item id={item_id} does not exist in database")
        return jsonify({"error": f"Item {item_id} not found"}), 404

    deleted = items_db.pop(item_id)
    Log("backend", "info", "service", f"Item id={item_id} name='{deleted['name']}' deleted successfully. Remaining items: {len(items_db)}")

    return jsonify({"message": f"Item {item_id} deleted successfully"}), 200


if __name__ == "__main__":
    Log("backend", "info", "config", "Flask application starting up on host=0.0.0.0 port=5000")
    app.run(debug=True, host="0.0.0.0", port=5000)