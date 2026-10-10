FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv==0.8.22
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev --python /usr/local/bin/python
COPY monitoring/demo.py /app/demo.py
ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT []
CMD ["proxy2vpn", "--compose-file", "/evidence/compose.yml", "agent", "metrics", "--bind-address", "127.0.0.1"]
