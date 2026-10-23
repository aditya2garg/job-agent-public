"""Skill taxonomy: canonical skill id -> category, market weight, and detection regexes.

`weight` is how much a mention of the skill should matter when judging a role in the
AI / data engineering family (1 = nice-to-know, 3 = defines the job).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Skill:
    id: str
    label: str
    category: str  # llm | ml | data | swe | cloud
    weight: float
    pattern: re.Pattern[str]


def _s(id: str, label: str, category: str, weight: float, pattern: str, case_sensitive: bool = False) -> Skill:
    flags = 0 if case_sensitive else re.IGNORECASE
    return Skill(id, label, category, weight, re.compile(pattern, flags))


SKILLS: list[Skill] = [
    # ---- LLM / agentic -------------------------------------------------
    _s("llm", "LLMs", "llm", 3, r"\bLLMs?\b|large language models?|foundation models?|\blanguage models?\b|\bgpt-?\d"),
    _s("genai", "Generative AI", "llm", 2, r"generative ai|\bgen ?ai\b|\bgenai\b"),
    _s("agents", "AI agents", "llm", 3,
       r"\bagentic\b|\b(ai|llm|autonomous|intelligent)\s+agents?\b|multi-?agent|agent (framework|orchestration|workflow|system|platform)s?\b|building agents|\bagents\b(?=.{0,60}\b(tool|llm|workflow|autonom))"),
    _s("mcp", "MCP", "llm", 2, r"\bMCP\b|model context protocol", True),
    _s("rag", "RAG", "llm", 3, r"\bRAG\b|retrieval[- ]augmented", True),
    _s("agent_frameworks", "LangChain / agent frameworks", "llm", 2,
       r"langchain|langgraph|llama-?index|crewai|autogen|semantic kernel|\bdspy\b|haystack|pydantic[- ]?ai|openai agents sdk|agent sdk"),
    _s("llm_apis", "LLM provider APIs", "llm", 2,
       r"\bopenai\b|anthropic|\bclaude\b|\bgemini\b|bedrock|vertex ai|azure openai|\bcortex\b|\bmistral\b|\bllama\s?\d?\b"),
    _s("prompt_eng", "Prompt engineering", "llm", 2, r"prompt(ing)? (engineering|design|optimi[sz]ation)|\bprompting\b|prompt engineer"),
    _s("tool_calling", "Tool / function calling", "llm", 2, r"function[- ]calling|tool[- ](use|calling)|tool integration"),
    _s("llm_evals", "LLM evals", "llm", 2.5,
       r"\bevals?\b|llm[- ]evaluation|evaluation (framework|harness|pipeline|suite)s?|llm-as-(a-)?judge|\bbenchmarking\b|red[- ]team|hallucination"),
    _s("guardrails", "Guardrails / safety", "llm", 1.5, r"guardrails?|responsible ai|ai safety|content moderation|safety (filter|system|classifier)"),
    _s("hitl", "Human-in-the-loop", "llm", 1, r"human[- ]in[- ]the[- ]loop|\bHITL\b|human feedback", True),
    _s("text_to_sql", "Text-to-SQL", "llm", 1.5, r"text[- ]to[- ]sql|nl2sql|natural language (to|queries? (to|over)) sql|conversational analytics|self[- ]serve analytics"),
    _s("vector_db", "Vector databases", "llm", 2,
       r"pinecone|weaviate|pgvector|\bfaiss\b|chroma(db)?\b|milvus|qdrant|vector (db|database|store|search|index)s?"),
    _s("embeddings", "Embeddings", "llm", 2, r"embeddings?|semantic search|similarity search|reranking|re-ranking"),
    _s("fine_tuning", "Fine-tuning / training LLMs", "llm", 2.5,
       r"fine[- ]?tun(e|ing)|\blora\b|\bqlora\b|\brlhf\b|\bdpo\b|\bsft\b|instruction tuning|\bpeft\b|pre-?training|post-?training|model distillation"),
    _s("llm_serving", "LLM inference / serving", "llm", 2,
       r"\bvllm\b|tensorrt|\btgi\b|sglang|model serving|inference (optimi[sz]ation|serving|infrastructure|engine)|triton inference"),
    _s("computer_vision", "Computer vision", "ml", 2, r"computer vision|image (recognition|classification)|object detection|\bocr\b|multimodal|video (understanding|generation)"),
    _s("speech", "Speech / audio", "ml", 1.5, r"speech recognition|\basr\b|text[- ]to[- ]speech|\btts\b|voice (ai|agent|assistant)s?"),
    # ---- ML / DS -------------------------------------------------------
    _s("pytorch", "PyTorch / JAX", "ml", 2.5, r"pytorch|\bjax\b|torch\b"),
    _s("tensorflow", "TensorFlow / Keras", "ml", 1.5, r"tensorflow|\bkeras\b"),
    _s("transformers", "Transformers / HF", "ml", 2, r"hugging ?face|\btransformers?\b|\bbert\b|attention mechanism"),
    _s("deep_learning", "Deep learning", "ml", 2, r"deep learning|neural networks?|\bdnn\b"),
    _s("nlp", "NLP", "ml", 2, r"\bNLP\b|natural language processing|named entity|text classification|\bNER\b", True),
    _s("ml_general", "Machine learning", "ml", 2,
       r"machine learning|\bML models?\b|scikit-?learn|xgboost|lightgbm|predictive model|classification|regression|clustering|supervised"),
    _s("mlops", "MLOps", "ml", 2, r"\bmlops\b|ml pipelines?|model (deployment|monitoring|registry)|mlflow|kubeflow|sagemaker|feature store|vertex pipelines|ml platform"),
    _s("statistics", "Statistics / experimentation", "ml", 1.5, r"statistic(s|al)|a/b test|experimentation|causal inference|hypothesis test"),
    _s("recsys", "Recommenders / ranking", "ml", 2, r"recommend(er|ation)s?|learning to rank|ranking (model|system)s?|search relevance"),
    # ---- data ----------------------------------------------------------
    _s("python", "Python", "data", 3, r"\bpython\b"),
    _s("sql", "SQL", "data", 3, r"\bsql\b|\bt-?sql\b|\bplsql\b|\bsparksql\b"),
    _s("pandas", "pandas / numpy", "data", 1, r"\bpandas\b|\bnumpy\b|\bpolars\b"),
    _s("snowflake", "Snowflake", "data", 2, r"snowflake|snowpark"),
    _s("dbt", "dbt", "data", 2, r"\bdbt\b"),
    _s("airflow", "Airflow", "data", 2, r"airflow"),
    _s("orchestration", "Dagster / Prefect", "data", 1, r"dagster|prefect|\bluigi\b|workflow orchestration|temporal\.io|\btemporal\b"),
    _s("spark", "Spark", "data", 2, r"\bspark\b|pyspark"),
    _s("kafka", "Kafka / messaging", "data", 2, r"kafka|kinesis|pub/?sub|rabbitmq|\bpulsar\b"),
    _s("streaming", "Streaming / real-time", "data", 1.5, r"\bflink\b|stream processing|real-time (data|pipeline|processing)|streaming (data|pipeline)"),
    _s("databricks", "Databricks", "data", 2, r"databricks|delta lake|unity catalog"),
    _s("lakehouse", "Lakehouse formats", "data", 1, r"\biceberg\b|\bhudi\b|lakehouse|parquet"),
    _s("bigquery", "BigQuery", "data", 1.5, r"bigquery"),
    _s("redshift", "Redshift", "data", 1, r"redshift"),
    _s("postgres", "Postgres / MySQL", "data", 1.5, r"postgres(ql)?|\bmysql\b|\baurora\b"),
    _s("elasticsearch", "Elasticsearch", "data", 1.5, r"elastic ?search|opensearch|\bsolr\b|\blucene\b"),
    _s("nosql", "NoSQL / cache", "data", 1, r"mongodb|dynamodb|cassandra|\bredis\b|\bscylla|\bneo4j\b|graph database"),
    _s("etl", "ETL / pipelines", "data", 2, r"\betl\b|\belt\b|data pipelines?|data ingestion|data integration|\bfivetran\b|\bairbyte\b"),
    _s("data_modeling", "Data modeling", "data", 2, r"data model(l)?ing|dimensional model|star schema|\bkimball\b|data vault"),
    _s("data_warehouse", "Data warehousing", "data", 2, r"data warehous|\bdwh\b|analytics (platform|infrastructure)|data platform|\bdata lake"),
    _s("semantic_layer", "Semantic layer", "data", 1.5, r"semantic (layer|model|view)s?|metrics layer|metric store|\blookml\b|cube\.dev|headless bi"),
    _s("data_quality", "Data quality / observability", "data", 1.5, r"data quality|data observability|great expectations|monte carlo|data contracts?|data testing"),
    _s("data_governance", "Data governance", "data", 1, r"data governance|\bmdm\b|master data|data catalog|data lineage|\bpii\b"),
    _s("bi_tools", "BI tools", "data", 1, r"tableau|looker|power ?bi|\bmetabase\b|\bsuperset\b|\bmode analytics|\bthoughtspot\b"),
    # ---- software ------------------------------------------------------
    _s("java", "Java", "swe", 2.5, r"\bjava\b(?!\s*script)|\bjvm\b|spring boot", False),
    _s("go", "Go", "swe", 2.5, r"\bgolang\b|(?<![A-Za-z])Go(?=\s*(?:[,/;)]|and\b|or\b|programming|language|lang\b|services|backend))", True),
    _s("rust", "Rust", "swe", 2, r"\brust\b(?=[\s,/;).]|$)", True),
    _s("cpp", "C / C++", "swe", 2.5, r"c\+\+|\bC/C\+\+|\bcpp\b"),
    _s("csharp", "C# / .NET", "swe", 2, r"c#|\.net\b|dotnet"),
    _s("ruby", "Ruby", "swe", 1.5, r"\bruby\b|\brails\b"),
    _s("scala", "Scala / Kotlin", "swe", 1.5, r"\bscala\b|\bkotlin\b"),
    _s("php", "PHP", "swe", 1, r"\bphp\b|\blaravel\b"),
    _s("javascript", "JavaScript", "swe", 1, r"javascript|\bnode\.?js\b|\bjs\b"),
    _s("typescript", "TypeScript", "swe", 1.5, r"typescript"),
    _s("react", "React / front-end", "swe", 1.5, r"\breact(\.js)?\b|\bnext\.js\b|\bvue\b|front-?end"),
    _s("node", "Node.js", "swe", 1, r"\bnode(\.js)?\b(?=.{0,20}(backend|server|api|express))|\bexpress\.js\b|\bnestjs\b"),
    _s("rest_api", "REST APIs", "swe", 1.5, r"\brest(ful)?\b|api (design|development)|\bapis?\b"),
    _s("python_web", "FastAPI / Flask / Django", "swe", 1.5, r"fastapi|flask|django"),
    _s("graphql", "GraphQL", "swe", 1, r"graphql"),
    _s("grpc", "gRPC", "swe", 1, r"\bgrpc\b|protobuf"),
    _s("microservices", "Microservices", "swe", 1.5, r"microservices?|service-oriented"),
    _s("distributed_systems", "Distributed systems", "swe", 2, r"distributed systems?|high[- ]scale|large[- ]scale systems?|low[- ]latency|concurrency"),
    _s("system_design", "System design", "swe", 1.5, r"system design|architect(ure|ing) (scalable|robust|production)|scalable (systems|architecture)"),
    _s("ci_cd", "CI/CD", "swe", 1, r"ci/cd|\bci\b|continuous (integration|delivery|deployment)|github actions|jenkins|\bargo ?cd\b"),
    _s("testing", "Testing", "swe", 1, r"unit test|integration test|test[- ]driven|\btdd\b|pytest"),
    # ---- cloud / devops ------------------------------------------------
    _s("aws", "AWS", "cloud", 2, r"\baws\b|amazon web services|\bec2\b|\bs3\b|aws lambda"),
    _s("gcp", "GCP", "cloud", 1.5, r"\bgcp\b|google cloud"),
    _s("azure", "Azure", "cloud", 1.5, r"\bazure\b"),
    _s("kubernetes", "Kubernetes", "cloud", 2, r"kubernetes|\bk8s\b|\bhelm\b|\beks\b|\bgke\b"),
    _s("docker", "Docker", "cloud", 1.5, r"\bdocker\b|container(s|ization|ized)\b"),
    _s("terraform", "Terraform / IaC", "cloud", 1.5, r"terraform|infrastructure as code|\biac\b|pulumi|cloudformation"),
    _s("observability", "Observability", "cloud", 1.5, r"datadog|splunk|grafana|prometheus|opentelemetry|observability|\bsentry\b|monitoring (and|&) alerting"),
    _s("linux", "Linux / shell", "cloud", 1, r"\blinux\b|\bbash\b|shell script"),
    _s("security_compliance", "Security / compliance", "cloud", 1, r"\bsoc ?2\b|\biam\b|least[- ]privilege|\bgdpr\b|\bhipaa\b|rbac|access control"),
]

BY_ID: dict[str, Skill] = {s.id: s for s in SKILLS}

AI_CATEGORIES = {"llm"}
DATA_CATEGORIES = {"data"}
