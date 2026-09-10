# Security Policy

## Scope

Psychology Evidence Agent is a local, human-in-the-loop research workflow. It
handles research questions, bibliographic metadata, locally supplied full text,
structured evidence cards and generated reports. It is not an access-control
bypass tool and it must not be used to retrieve paywalled material without
permission.

## Supported versions

Only the latest commit on the default branch is treated as actively maintained
for security fixes. This repository is currently private; release artifacts and
research data are not intended to be published by default.

## Reporting a vulnerability

Please do not open a public issue containing secrets, private papers, personal
data or a reproducible exploit. Contact the project maintainer through the
private repository channel and include:

- a short description of the affected component;
- reproduction steps using synthetic or public data only;
- the possible impact; and
- a suggested mitigation, if known.

Remove API keys, tokens, private URLs and identifying research data before
sharing any diagnostic material.

## Security boundaries

- API keys and local service passwords must be supplied through the environment
  or an interactive session; do not commit them to the repository.
- `PEA_API_KEY` protects the local browser API. It is not an OpenAI, Codex or
  literature-provider credential.
- The local service binds to `127.0.0.1` by default. If it is placed behind a
  tunnel or reverse proxy, configure authentication and HTTPS at that boundary.
- Paper content is treated as untrusted input. It must not be interpreted as
  instructions, and structured model output remains subject to JSON Schema and
  inference-boundary validation.
- CI uses mocked paper APIs and mocked/read-only model adapters. CI must not
  access real paper services or model endpoints.
- Only legally obtained full text should be placed under `data/raw/`; raw,
  processed and log outputs are ignored by Git and should remain local.

## Dependency and configuration changes

When adding a provider or dependency, document its data flow, credentials,
network destination, retry behavior and failure mode before enabling it in the
default workflow.
