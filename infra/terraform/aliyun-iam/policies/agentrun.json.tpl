{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "agentrun:CreateAgentRuntime",
        "agentrun:GetAgentRuntime",
        "agentrun:DeleteAgentRuntime",
        "agentrun:CreateAgentRuntimeEndpoint",
        "agentrun:DeleteAgentRuntimeEndpoint"
        %{ if enable_data_plane }
        ,
        "agentrun:InvokeRuntime",
        "agentrun:CreateMemory",
        "agentrun:RetrieveMemory",
        "agentrun:UpdateMemory",
        "agentrun:ActivateTemplateMCP"
        %{ endif }
      ],
      "Resource": "acs:agentrun:*:*:*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "oss:PutObject",
        "oss:GetObject",
        "oss:DeleteObject"
      ],
      "Resource": "acs:oss:*:*:${bucket}/clousight-bench/*"
    }
  ]
}
