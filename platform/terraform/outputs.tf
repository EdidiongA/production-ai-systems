output "cluster_name" {
  value = aws_eks_cluster.this.name
}

output "cluster_endpoint" {
  value = aws_eks_cluster.this.endpoint
}

output "configure_kubectl" {
  description = "Run this after apply."
  value       = "aws eks update-kubeconfig --region ${var.region} --name ${aws_eks_cluster.this.name}"
}

output "ecr_repository_urls" {
  value = { for k, r in aws_ecr_repository.repos : k => r.repository_url }
}

output "oidc_provider_arn" {
  description = "For wiring IRSA roles and the GitHub Actions deploy role."
  value       = aws_iam_openid_connect_provider.this.arn
}
