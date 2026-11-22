FROM ghcr.io/foundry-rs/foundry:v1.7.1@sha256:8347b728d5d393dac1c018691b36f506d23b9dcd78341d40ea0fcb11c3a19cdd AS foundry
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
COPY --from=foundry /usr/local/bin/anvil /usr/local/bin/anvil
RUN anvil --version && python -m pip install --disable-pip-version-check --no-cache-dir --only-binary=:all: \
    matplotlib==3.11.2 numpy==2.5.5 contourpy==1.4.0 cycler==0.12.1 fonttools==4.68.0 \
    kiwisolver==1.5.1 packaging==26.3 pillow==13.0.0 pyparsing==3.3.3 python-dateutil==4.0.0 tzdata==2026.6
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MPLCONFIGDIR=/tmp/matplotlib HOME=/tmp
WORKDIR /experiment
