FROM python:3.12-slim

WORKDIR /app

COPY ops-agent/requirements.txt ops-agent/requirements.txt
RUN pip install --no-cache-dir -r ops-agent/requirements.txt

COPY mirror_registry_core/ mirror_registry_core/
COPY ops_agent/ ops_agent/

CMD ["python", "-m", "ops_agent.agent"]
