FROM docker:28.3.2-cli AS dockercli
FROM python:3.12.9-slim-bookworm
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=dockercli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose
WORKDIR /workspace
COPY analysis/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY scripts /opt/benchmark/scripts
COPY analysis /opt/benchmark/analysis
ENV MPLCONFIGDIR=/tmp/matplotlib PYTHONUNBUFFERED=1
CMD ["python", "analysis/pipeline.py"]
