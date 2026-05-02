import os
import sys
import requests
from flask import Flask, jsonify
from dotenv import load_dotenv

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'logging_middleware'))
from logging_middleware import Log

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

app = Flask(__name__)

BASE_URL = "http://20.207.122.201"
PORT = 5001

auth_token = None
depots_cache = []
vehicles_cache = []


def authenticate():
    global auth_token
    if auth_token:
        return auth_token
    resp = requests.post(
        f"{BASE_URL}/evaluation-service/auth",
        json={
            "email": os.getenv("EMAIL"),
            "name": os.getenv("NAME"),
            "rollNo": os.getenv("ROLL_NO"),
            "accessCode": os.getenv("ACCESS_CODE"),
            "clientID": os.getenv("CLIENT_ID"),
            "clientSecret": os.getenv("CLIENT_SECRET"),
        },
        timeout=15
    )
    resp.raise_for_status()
    auth_token = resp.json()["access_token"]
    Log("backend", "info", "auth", "Token acquired")
    return auth_token


def ensure_auth():
    if not auth_token:
        authenticate()
    return auth_token


def fetch_depots():
    global depots_cache
    token = ensure_auth()
    resp = requests.get(
        f"{BASE_URL}/evaluation-service/depots",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15
    )
    resp.raise_for_status()
    depots_cache = resp.json()["depots"]
    Log("backend", "info", "service", f"Depots loaded: {len(depots_cache)}")
    return depots_cache


def fetch_vehicles():
    global vehicles_cache
    token = ensure_auth()
    resp = requests.get(
        f"{BASE_URL}/evaluation-service/vehicles",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15
    )
    resp.raise_for_status()
    vehicles_cache = resp.json()["vehicles"]
    Log("backend", "info", "service", f"Vehicles loaded: {len(vehicles_cache)}")
    return vehicles_cache


def knapsack_solve(items, capacity):
    n = len(items)
    dp = [[0] * (capacity + 1) for _ in range(n + 1)]

    for i in range(1, n + 1):
        dur = items[i - 1]["duration"]
        imp = items[i - 1]["impact"]
        for w in range(capacity + 1):
            dp[i][w] = dp[i - 1][w]
            if w >= dur:
                dp[i][w] = max(dp[i][w], dp[i - 1][w - dur] + imp)

    selected = []
    w = capacity
    for i in range(n, 0, -1):
        if dp[i][w] != dp[i - 1][w]:
            selected.append(items[i - 1])
            w -= items[i - 1]["duration"]

    max_impact = dp[n][capacity]
    total_duration = sum(t["duration"] for t in selected)

    return {
        "maxImpact": max_impact,
        "totalDuration": total_duration,
        "remainingCapacity": capacity - total_duration,
        "selectedTasks": [t["TaskId"] for t in selected]
    }


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service": "Vehicle Maintenance Scheduler",
        "status": "running",
        "endpoints": [
            "GET /evaluation-service/depots",
            "GET /evaluation-service/vehicles",
            "GET /evaluation-service/schedule"
        ]
    })


@app.route("/evaluation-service/depots", methods=["GET"])
def get_depots():
    try:
        Log("backend", "info", "handler", "GET /evaluation-service/depots")
        if not depots_cache:
            fetch_depots()
        return jsonify({"depots": depots_cache})
    except Exception as err:
        Log("backend", "error", "handler", f"Failed: {str(err)}")
        return jsonify({"error": str(err)}), 500


@app.route("/evaluation-service/vehicles", methods=["GET"])
def get_vehicles():
    try:
        Log("backend", "info", "handler", "GET /evaluation-service/vehicles")
        if not vehicles_cache:
            fetch_vehicles()
        return jsonify({"vehicles": vehicles_cache})
    except Exception as err:
        Log("backend", "error", "handler", f"Failed: {str(err)}")
        return jsonify({"error": str(err)}), 500


@app.route("/evaluation-service/schedule", methods=["GET"])
def get_schedule():
    try:
        Log("backend", "info", "handler", "GET /evaluation-service/schedule")
        if not depots_cache:
            fetch_depots()
        if not vehicles_cache:
            fetch_vehicles()

        results = []
        for depot in depots_cache:
            items = [
                {"TaskId": v["TaskID"], "duration": v["Duration"], "impact": v["Impact"]}
                for v in vehicles_cache
            ]
            solution = knapsack_solve(items, depot["MechanicHours"])
            results.append({
                "depotId": depot["ID"],
                "mechanicHoursAvailable": depot["MechanicHours"],
                "maxTotalImpact": solution["maxImpact"],
                "totalDurationUsed": solution["totalDuration"],
                "remainingHours": solution["remainingCapacity"],
                "tasksSelected": len(solution["selectedTasks"]),
                "selectedTasks": solution["selectedTasks"]
            })

        Log("backend", "info", "handler", "Schedule computed for all depots")
        return jsonify({"schedules": results})
    except Exception as err:
        Log("backend", "error", "handler", f"Failed: {str(err)}")
        return jsonify({"error": str(err)}), 500


@app.before_request
def log_request():
    from flask import request
    Log("backend", "info", "middleware", f"Incoming {request.method} {request.path} from {request.remote_addr}")


@app.after_request
def log_response(response):
    from flask import request
    Log("backend", "info", "middleware", f"Response {response.status_code} for {request.method} {request.path}")
    return response


@app.errorhandler(404)
def not_found(e):
    from flask import request
    Log("backend", "warn", "route", f"404 Not Found: {request.path}")
    return jsonify({"error": "Route not found"}), 404


@app.errorhandler(500)
def server_error(e):
    from flask import request
    Log("backend", "fatal", "handler", f"500 Internal Server Error on {request.path}: {str(e)}")
    return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    print(f"Server running at http://localhost:{PORT}")
    print(f"  http://localhost:{PORT}/evaluation-service/depots")
    print(f"  http://localhost:{PORT}/evaluation-service/vehicles")
    print(f"  http://localhost:{PORT}/evaluation-service/schedule")

    try:
        authenticate()
        fetch_depots()
        fetch_vehicles()
        print("Ready.")
    except Exception as err:
        print(f"Init error: {err}")

    app.run(debug=True, host="0.0.0.0", port=PORT)
