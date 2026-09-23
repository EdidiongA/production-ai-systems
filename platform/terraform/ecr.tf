resource "aws_ecr_repository" "repos" {
  for_each = toset(var.ecr_repositories)

  name                 = each.value
  image_tag_mutability = "IMMUTABLE" # a tag is a contract; rebuilds get new tags

  image_scanning_configuration {
    scan_on_push = true
  }
}

# Keep the last 10 images per repo - portfolio budgets don't need an
# unbounded registry bill.
resource "aws_ecr_lifecycle_policy" "keep_last_10" {
  for_each   = aws_ecr_repository.repos
  repository = each.value.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep last 10 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}
