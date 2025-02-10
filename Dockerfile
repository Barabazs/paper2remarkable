FROM golang:buster AS rmapi

ENV GOPATH=/go \
    PATH=${GOPATH}/bin:/usr/local/go/bin:$PATH \
    RMAPIREPO=github.com/ddvk/rmapi

RUN git clone https://${RMAPIREPO} && cd rmapi && go install


FROM python:3.11-slim-bullseye

# rmapi
COPY --from=rmapi /go/bin/rmapi /usr/bin/rmapi

# needed to install openjdk-11-jre-headless
RUN mkdir -p /usr/share/man/man1

# imagemagick, pdftk, ghostscript, pdfcrop, weasyprint
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
    libmagickwand-dev \
    pdftk \
    ghostscript \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Install uv using the official distroless image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy build files first for better layer caching
COPY pyproject.toml MANIFEST.in README.md /paper2remarkable/
WORKDIR /paper2remarkable

# Install dependencies first (without installing project)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-editable

# Copy the project source
COPY paper2remarkable /paper2remarkable/paper2remarkable

# Now install the project
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system -e .

# Verify installed version
RUN p2r --version

RUN useradd -u 1000 -m -U user
USER user
ENV USER=user
WORKDIR /home/user

ENTRYPOINT ["p2r"]
