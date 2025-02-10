FROM golang:buster AS rmapi

ENV GOPATH /go
ENV PATH ${GOPATH}/bin:/usr/local/go/bin:$PATH
ENV RMAPIREPO github.com/ddvk/rmapi

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
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# Install paper2remarkable using uv
RUN uv pip install --system paper2remarkable && rm -rf /root/.cache/uv

RUN useradd -u 1000 -m -U user

USER user

ENV USER user

WORKDIR /home/user

ENTRYPOINT ["p2r"]
