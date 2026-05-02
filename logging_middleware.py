from flask import requests

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