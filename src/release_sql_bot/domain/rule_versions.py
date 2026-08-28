"""RuleReader 不可变规则版本的真实 MongoDB 包装契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictInt,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
RuleSchemaVersion = Literal["1.0.0", "2.0.0"]


class RuleVersionModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class RuleVersionSource(RuleVersionModel):
    source_name: str = Field(min_length=1, max_length=512)
    relative_path: str = Field(min_length=1, max_length=2_000)
    character_count: StrictInt = Field(ge=0)
    sha256: Sha256


class RuleVersionParser(RuleVersionModel):
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    prompt_version: str = Field(min_length=1, max_length=128)
    parser_version: str = Field(min_length=1, max_length=128)


class RuleVersionRule(RuleVersionModel):
    rule_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    title: str = Field(min_length=1, max_length=500)
    scope: str = Field(min_length=1, max_length=2_000)
    entity_type: str | None = Field(default=None, min_length=1, max_length=160)
    source_views: tuple[str, ...]
    root_condition: dict[str, JsonValue]
    required_facts: tuple[dict[str, JsonValue], ...]
    field_mappings: tuple[dict[str, JsonValue], ...]
    test_cases: tuple[dict[str, JsonValue], ...]
    responsible_roles: tuple[str, ...]
    failure_reasons: tuple[str, ...]
    exception_notes: tuple[str, ...]
    recommendations: tuple[str, ...]
    warnings: tuple[str, ...]


class RuleVersionDocument(RuleVersionModel):
    schema_version: RuleSchemaVersion
    rule_version: str = Field(min_length=1, max_length=320)
    status: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    executable: Literal[False]
    generated_at: datetime
    source: RuleVersionSource
    parser: RuleVersionParser
    rule: RuleVersionRule

    @field_validator("generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("document.generatedAt 必须包含时区")
        return value

    @model_validator(mode="after")
    def validate_version_specific_rule_shape(self) -> Self:
        if self.schema_version == "1.0.0" and self.rule.entity_type is not None:
            raise ValueError("Schema 1.0.0 规则不能定义 entityType")
        if self.schema_version == "2.0.0" and self.rule.entity_type is None:
            raise ValueError("Schema 2.0.0 规则必须定义 entityType")
        return self


class StoredRuleVersion(RuleVersionModel):
    rule_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    rule_version: str = Field(min_length=1, max_length=320)
    schema_version: RuleSchemaVersion
    source_sha256: Sha256
    parser_version: str = Field(min_length=1, max_length=128)
    status: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    executable: Literal[False]
    generated_at: datetime
    stored_at: datetime
    document: RuleVersionDocument

    @field_validator("generated_at", "stored_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("规则版本存储时间必须包含时区")
        return value

    @model_validator(mode="after")
    def validate_envelope_references(self) -> Self:
        mismatches: list[str] = []
        if self.rule_id != self.document.rule.rule_id:
            mismatches.append("rule_id")
        if self.rule_version != self.document.rule_version:
            mismatches.append("rule_version")
        if self.schema_version != self.document.schema_version:
            mismatches.append("schema_version")
        if self.source_sha256 != self.document.source.sha256:
            mismatches.append("source_sha256")
        if self.parser_version != self.document.parser.parser_version:
            mismatches.append("parser_version")
        if self.status != self.document.status:
            mismatches.append("status")
        if self.executable != self.document.executable:
            mismatches.append("executable")
        if _bson_millisecond(self.generated_at) != _bson_millisecond(self.document.generated_at):
            mismatches.append("generated_at")
        if mismatches:
            raise ValueError(f"规则版本包装引用不一致：{', '.join(mismatches)}")
        if self.stored_at < self.generated_at:
            raise ValueError("stored_at 不能早于 generated_at")
        return self


def _bson_millisecond(value: datetime) -> int:
    """MongoDB BSON 日期只保留毫秒，比较时忽略导出字符串中的额外微秒。"""

    return int(value.timestamp() * 1_000)
