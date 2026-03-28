package clawops.routing

default tier = "deny"

tier = "reader" {
  input.kind == "hostile_content"
}

tier = "coder" {
  input.kind == "code_change"
}

tier = "reviewer" {
  input.kind == "security_review"
}
