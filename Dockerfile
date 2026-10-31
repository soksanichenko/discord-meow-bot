# Stage 1: Build Python venv
FROM almalinux:10 AS python-builder
RUN dnf install -y python3 python3-devel gcc && dnf clean all
WORKDIR /code
COPY sources/requirements.txt sources/requirements.txt
RUN python3 -m venv venv && \
    venv/bin/pip install --upgrade pip && \
    venv/bin/pip install -r sources/requirements.txt

# Stage 2: Runtime
FROM almalinux:10
RUN dnf install -y python3 && dnf clean all
RUN groupadd --gid 1000 meowbot && \
    useradd --uid 1000 --gid meowbot --create-home --shell /sbin/nologin meowbot
ENV PYTHONPATH="."
WORKDIR /code
COPY --from=python-builder /code/venv ./venv
COPY sources/ ./sources/
RUN chown -R meowbot:meowbot /code
USER 1000
ENTRYPOINT ["bash"]
