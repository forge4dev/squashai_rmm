# How it was built

## Tools used

OpenAI Codex was used as the coding assistant for architecture exploration,
implementation drafts, repository inspection, test generation, debugging, and
documentation. The OpenAI API is also used at runtime by the optional minimal AI
diagnostic driver. No additional autonomous coding agents were used to divide the
repository work.

The human developer made the scope and product decisions, including Python for
the control plane, C# for the native Windows service, WiX for the MSI, SQLite for
the assessment deployment, HTTPS despite the lab setting, and a read-only AI
driver. The developer also performed Windows VM deployment, certificate trust,
MSI enrollment, agent connectivity, and dashboard validation. AI-proposed code
and commands were reviewed and exercised locally rather than accepted only from
generated text.

## Work breakdown

Work was split into vertical slices so each stage could be tested through its
public boundary:

1. Defined API contracts, execution states, idempotency behavior, SQLite models,
   authentication, audit records, encryption, and timeout workers.
2. Added the authenticated outbound WebSocket and tested an execution round trip
   with a simulated agent.
3. Built the C# Windows service with DPAPI identity storage, bounded PowerShell,
   hash reporting, reconnection, and an encrypted endpoint idempotency journal.
4. Packaged the self-contained agent in a WiX MSI with silent enrollment,
   automatic service startup, upgrade, rollback, and uninstall behavior.
5. Added the OpenAI diagnostic loop, CLI transcript, and Streamlit demonstration
   UI with per-step scripts and timing.
6. Added inventory and structured event-log access as extra-credit features,
   reusing the existing authenticated execution lifecycle.
7. Rehearsed installation, TLS trust, enrollment, timeout, offline delivery, and
   AI-driven diagnosis on Windows, then tightened the setup and threat-model
   documentation around issues found during deployment.

Testing combined FastAPI/TestClient integration tests, security/property tests,
SQLite configuration tests, .NET unit tests, Streamlit AppTest coverage, Ruff,
the .NET compiler, WiX ICE validation, and manual Windows VM checks. At submission
time the automated suites contained 16 control-plane tests, 4 agent tests, and 7
AI-driver/UI tests, and the final MSI passed WiX validation.

## Time

Repository timestamps span roughly two calendar days. Active effort was not
tracked with a timer, so the best estimate is **12–16 hours** across design,
implementation, tests, Windows deployment/debugging, the demo UI, and written
deliverables. This estimate includes the human review and VM validation time, not
just model response time.

