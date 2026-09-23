# Security-log redaction: primary sources for teaching

Scope: when an AI-agent system should redact telemetry, what useful metadata can remain, and how access and retention should be controlled.

## Sources and teaching notes

- **OWASP Logging Cheat Sheet — exclude or transform sensitive data.** Passwords, session identifiers, access tokens, connection strings, cryptographic keys, payment data, sensitive PII, and data above the log store's classification should not be recorded directly; remove, mask, sanitize, hash, or encrypt them. OWASP also recommends minimization/pseudonymization for identifiers and says sanitization may occur after collection but before display. Logs need protection against unauthorized access, modification, deletion, and tampering. Keep security events such as authentication outcomes, authorization failures, administrative actions, and access to sensitive data, with a classification flag when useful.
  Source: [OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html) ("Data to exclude", "Customizable logging", and "Protection").
  Teaching use: redact at the producer boundary whenever possible; allow only a documented minimum event set through, and explain that a stable pseudonym can preserve correlation without exposing the identifier.

- **OWASP Logging Vocabulary — event context is optional and privacy-sensitive.** OWASP's example vocabulary treats fields after the event type as optional and explicitly warns that an IP address can be PII when combined with other data and subject to deletion requests.
  Source: [OWASP Logging Vocabulary Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Vocabulary_Cheat_Sheet.html) ("Data Privacy").
  Teaching use: retain the smallest useful context: timestamp, event type, severity, outcome, actor/service pseudonym, request/trace correlation ID, and resource/action identifiers; omit raw prompts, tool arguments, tokens, and unnecessary user attributes.

- **OpenTelemetry sensitive-data guidance — minimize before collection, then scrub at processing/export boundaries.** OpenTelemetry puts responsibility on the implementer, lists PII, credentials, session tokens, financial, health, and behavior data as potentially sensitive, and recommends collecting only what serves observability. Collector `attribute`, `filter`, `redaction`, and `transform` processors can delete, hash, filter, or transform data; hashing predictable IDs may still be reversible. Its URL semantic conventions say user/password information MUST NOT be recorded and known sensitive query values MUST be scrubbed before capturing `url.query`; HTTP guidance says sensitive query content SHOULD be scrubbed.
  Sources: [OpenTelemetry: Handling sensitive data](https://opentelemetry.io/docs/security/handling-sensitive-data/), [URL semantic conventions](https://opentelemetry.io/docs/specs/semconv/url/), and [HTTP semantic conventions](https://opentelemetry.io/docs/specs/semconv/http/http-spans/).
  Teaching use: show a two-line policy: deny collection by default, then apply a final allowlist/redaction processor before export to a shared backend. Explain that post-collection scrubbing is a containment fallback, not permission to emit secrets upstream.

- **Langfuse masking — redact before trace data leaves the application.** Langfuse documents masking trace/observation inputs, outputs, metadata, and OpenTelemetry span attributes, with a recommended `mask_otel_spans` hook running at export stage before data is sent to Langfuse.
  Source: [Langfuse: Masking sensitive LLM data](https://langfuse.com/docs/observability/features/masking).
  Teaching use: place the hook after instrumentation has assembled the span but before network export; test it with prompts, tool arguments, model outputs, and metadata because any of those can carry secrets or PII.

- **NIST SP 800-53 — constrain contents, access, protection, and retention.** AU-3(1) calls for limiting PII elements in audit records; AU-9 includes cryptographic protection, separate storage, read-only access, and access by a subset of privileged users; AU-11 requires an organization-defined retention period consistent with legal and operational needs and long-term retrieval when required.
  Source: [NIST SP 800-53 Rev. 5 (current publication page)](https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final) (Audit and Accountability controls AU-3, AU-9, AU-11; authoritative control downloads are linked there).
  Teaching use: make retention an explicit policy field, enforce least-privilege/role-based access and immutable or append-only storage for security logs, and delete or age out records when the policy period ends.

- **NIST SP 800-92 — log management is a lifecycle.** NIST describes log management as generating, transmitting, storing, accessing, and disposing of logs, and warns that logs can capture passwords or email content, creating confidentiality and privacy risks in transit and storage.
  Source: [NIST SP 800-92](https://csrc.nist.gov/pubs/sp/800/92/final) and [PDF](https://nvlpubs.nist.gov/nistpubs/legacy/SP/nistspecialpublication800-92.Pdf).
  Teaching use: teach redaction as part of the whole lifecycle: prevent capture, protect transport/storage, audit access, and dispose on schedule—not merely mask a dashboard view.

## Minimal policy to derive for an AI-agent lesson

`allowlisted event metadata → producer-side redaction → export-time defense-in-depth scrub → encrypted, least-privilege, tamper-resistant storage → policy-based retention/disposal`

Never emit credentials, session/access tokens, raw secrets, or unneeded prompt/tool/output content. Preserve only metadata needed to correlate and investigate (time, event, severity, outcome, service/actor pseudonym, trace/request ID, and resource/action), and document the retention and authorized-reader roles.
