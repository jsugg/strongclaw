package clawops.side_effects

default allow = false

allow {
  input.trust_zone == "automation"
  input.action == "webhook.post"
  startswith(input.target, "https://example.internal/")
}

require_approval {
  input.action == "github.comment.create"
}
