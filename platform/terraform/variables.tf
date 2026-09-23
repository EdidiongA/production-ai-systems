variable "region" {
  description = "AWS region. eu-west-1 keeps latency reasonable from Lagos while staying in a full-service region."
  type        = string
  default     = "eu-west-1"
}

variable "cluster_name" {
  type    = string
  default = "ml-platform"
}

variable "kubernetes_version" {
  type    = string
  default = "1.31"
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "node_instance_types" {
  description = "SPOT-eligible instance types. Multiple types = better spot availability."
  type        = list(string)
  default     = ["t3.medium", "t3a.medium"]
}

variable "node_desired" {
  type    = number
  default = 2
}

variable "node_max" {
  type    = number
  default = 4
}

variable "ecr_repositories" {
  description = "One repository per deployable system."
  type        = list(string)
  default     = ["rag-api", "asset-agent", "ticket-pipeline"]
}
