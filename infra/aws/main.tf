# ECS Fargate: the API behind an HTTPS load balancer, and the worker with no inbound access, both
# from one image. Secrets in Secrets Manager under a customer-managed KMS key; Postgres on RDS in
# private subnets. The API token's value is set out of band. The worker opens arbitrary public
# websites; security groups cannot deny ranges, so the private-range rule lives in the app's egress
# proxy and the worker's egress is limited to web ports and Postgres. Deploy-ready, not deployed.

data "aws_caller_identity" "current" {}

# --- Encryption key for secrets and logs ---
data "aws_iam_policy_document" "kms" {
  #checkov:skip=CKV_AWS_109:Key policies must grant the account root; scoping happens in IAM.
  #checkov:skip=CKV_AWS_111:As above, the standard root statement of a key policy.
  #checkov:skip=CKV_AWS_356:Resource "*" in a key policy means "this key".
  statement {
    sid       = "AccountAdmin"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }
  statement {
    sid       = "CloudWatchLogs"
    actions   = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["logs.${var.region}.amazonaws.com"]
    }
  }
}

resource "aws_kms_key" "this" {
  description         = "${var.name} secrets and logs"
  enable_key_rotation = true
  policy              = data.aws_iam_policy_document.kms.json
}

# --- Secrets ---
locals {
  app_secrets = ["api-token"]
}

resource "aws_secretsmanager_secret" "app" {
  #checkov:skip=CKV2_AWS_57:The API token is rotated by hand with the client; no rotation Lambda.
  for_each                = toset(local.app_secrets)
  name                    = "${var.name}/${each.value}"
  kms_key_id              = aws_kms_key.this.arn
  recovery_window_in_days = 7
}

resource "random_password" "db" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "database_url" {
  #checkov:skip=CKV2_AWS_57:Rotated together with the RDS password.
  name                    = "${var.name}/database-url"
  kms_key_id              = aws_kms_key.this.arn
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id     = aws_secretsmanager_secret.database_url.id
  secret_string = "postgresql+psycopg://audit:${random_password.db.result}@${aws_db_instance.this.address}:5432/audit?sslmode=require"
}

# --- Network access ---
resource "aws_security_group" "alb" {
  name        = "${var.name}-alb"
  description = "HTTPS from the internet to the API load balancer"
  vpc_id      = var.vpc_id
  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    description = "To API tasks"
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"]
  }
}

resource "aws_security_group" "tasks" {
  #checkov:skip=CKV_AWS_382:The worker audits arbitrary public websites, so web ports must reach the internet; private ranges are refused by the in-app egress proxy.
  name        = "${var.name}-tasks"
  description = "API and worker tasks"
  vpc_id      = var.vpc_id
  ingress {
    description     = "API from the load balancer"
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    description = "HTTPS to audited sites and AWS APIs"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    description = "HTTP to audited sites that have not moved to HTTPS"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    description = "Postgres"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"]
  }
}

resource "aws_security_group" "db" {
  name        = "${var.name}-db"
  description = "Postgres from the tasks only"
  vpc_id      = var.vpc_id
  ingress {
    description     = "Postgres from tasks"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.tasks.id]
  }
}

# --- Database ---
resource "aws_db_subnet_group" "this" {
  name       = "${var.name}-db"
  subnet_ids = var.private_subnet_ids
}

resource "aws_db_instance" "this" {
  #checkov:skip=CKV_AWS_157:Multi-AZ is a production cost decision, not needed for the pilot.
  #checkov:skip=CKV_AWS_161:The app connects with a password from Secrets Manager; IAM auth is a later hardening step.
  #checkov:skip=CKV2_AWS_30:Query logging would record audit targets and reports; only error logs are exported.
  #checkov:skip=CKV_AWS_118:Performance Insights is on; enhanced OS monitoring needs its own IAM role, a production step.
  identifier                      = "${var.name}-db"
  engine                          = "postgres"
  engine_version                  = "17"
  instance_class                  = "db.t4g.micro"
  allocated_storage               = 20
  db_name                         = "audit"
  username                        = "audit"
  password                        = random_password.db.result
  db_subnet_group_name            = aws_db_subnet_group.this.name
  vpc_security_group_ids          = [aws_security_group.db.id]
  storage_encrypted               = true
  kms_key_id                      = aws_kms_key.this.arn
  backup_retention_period         = 14
  deletion_protection             = true
  skip_final_snapshot             = false
  final_snapshot_identifier       = "${var.name}-db-final"
  copy_tags_to_snapshot           = true
  auto_minor_version_upgrade      = true
  publicly_accessible             = false
  performance_insights_enabled    = true
  performance_insights_kms_key_id = aws_kms_key.this.arn
  monitoring_interval             = 0
  enabled_cloudwatch_logs_exports = ["postgresql"]
}

# --- Logs ---
resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${var.name}"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.this.arn
}

# --- IAM ---
data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "read_secrets" {
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    resources = concat(
      [for s in aws_secretsmanager_secret.app : s.arn],
      [aws_secretsmanager_secret.database_url.arn],
    )
  }
  statement {
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.this.arn]
  }
}

resource "aws_iam_role_policy" "read_secrets" {
  name   = "read-app-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.read_secrets.json
}

resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

# --- ECS ---
resource "aws_ecs_cluster" "this" {
  name = var.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

locals {
  secret_env = merge(
    { API_TOKEN = aws_secretsmanager_secret.app["api-token"].arn },
    { DATABASE_URL = aws_secretsmanager_secret.database_url.arn },
  )
  roles = {
    # The worker runs Chromium: one vCPU and 2 GiB for two audits at a time.
    api    = { command = ["ta-api"], count = 1, cpu = 512, memory = 1024 }
    worker = { command = ["ta-worker"], count = var.worker_count, cpu = 1024, memory = 2048 }
  }
}

resource "aws_ecs_task_definition" "this" {
  for_each                 = local.roles
  family                   = "${var.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  container_definitions = jsonencode([{
    name                   = each.key
    image                  = var.image
    command                = each.value.command
    essential              = true
    readonlyRootFilesystem = true
    user                   = "10001"
    portMappings           = each.key == "api" ? [{ containerPort = 8080, protocol = "tcp" }] : []
    # Root filesystem stays read-only; Chromium's profile and scratch files go to an ephemeral /tmp.
    mountPoints = [{ sourceVolume = "tmp", containerPath = "/tmp", readOnly = false }]
    environment = [
      { name = "HOME", value = "/tmp" },
      { name = "PREPARED_BY", value = var.prepared_by },
      { name = "WORKER_CONCURRENCY", value = "2" },
    ]
    secrets = [for k, arn in local.secret_env : { name = k, valueFrom = arn }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.this.name
        awslogs-region        = var.region
        awslogs-stream-prefix = each.key
      }
    }
  }])
  volume {
    name = "tmp"
  }
}

resource "aws_ecs_service" "this" {
  for_each        = local.roles
  name            = "${var.name}-${each.key}"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.this[each.key].arn
  desired_count   = each.value.count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }

  dynamic "load_balancer" {
    for_each = each.key == "api" ? [1] : []
    content {
      target_group_arn = aws_lb_target_group.api.arn
      container_name   = "api"
      container_port   = 8080
    }
  }
}

# --- Load balancer ---
resource "aws_lb" "api" { # nosemgrep: aws-elb-access-logs-not-enabled -- needs the org logging bucket (see CKV_AWS_91 skip)
  #checkov:skip=CKV_AWS_91:Access logs need a dedicated S3 bucket; add with the organisation's logging account.
  #checkov:skip=CKV2_AWS_28:WAF is attached at the organisation's edge (or add aws_wafv2_web_acl_association).
  #checkov:skip=CKV2_AWS_20:HTTP is not exposed at all; only the HTTPS listener exists.
  name                       = "${var.name}-api"
  load_balancer_type         = "application"
  internal                   = false
  subnets                    = var.public_subnet_ids
  security_groups            = [aws_security_group.alb.id]
  drop_invalid_header_fields = true
  enable_deletion_protection = true
}

resource "aws_lb_target_group" "api" {
  #checkov:skip=CKV_AWS_378:TLS terminates at the load balancer; tasks are in private subnets.
  name        = "${var.name}-api"
  port        = 8080
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id
  health_check {
    path    = "/healthz"
    matcher = "200"
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.api.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}
