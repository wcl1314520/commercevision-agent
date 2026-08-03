"""Five versioned MCP adapters over existing application interfaces."""

from __future__ import annotations

from typing import Protocol

from commercevision_contracts import (
    AssetsGetTemporaryReferenceInputV1,
    AssetsGetTemporaryReferenceOutputV1,
    AssetsSearchInputV1,
    AssetsSearchOutputV1,
    BrandGetProfileInputV1,
    BrandGetProfileOutputV1,
    BrandProfileResponseV1,
    BrandProfileVersionResponseV1,
    CatalogGetProductBriefInputV1,
    CatalogGetProductBriefOutputV1,
    CatalogGetProductInputV1,
    CatalogGetProductOutputV1,
    McpToolIdentityV1,
    ProductBriefResponseV1,
    ProductResponseV1,
    RetrievalQueryV1,
    RetrievalResponseV1,
    RetrievalTemporaryReferenceV1,
)
from commercevision_domain import NotFoundError
from commercevision_tool_runtime import (
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionError,
    ToolExecutionGateway,
    ToolInvocation,
    ToolRegistry,
    ToolResult,
)
from commercevision_tool_runtime.gateway import stable_tool_key
from commercevision_tool_runtime.policy import ToolPolicy


class CatalogReadPort(Protocol):
    def get_product(self, *, workspace_id: str, product_id: str) -> ProductResponseV1: ...


class ProductBriefReadPort(Protocol):
    def get(self, *, workspace_id: str, product_brief_id: str) -> ProductBriefResponseV1: ...


class BrandProfileReadPort(Protocol):
    def get(self, *, workspace_id: str, profile_id: str) -> BrandProfileResponseV1: ...

    def get_version(
        self, *, workspace_id: str, profile_id: str, version_number: int
    ) -> BrandProfileVersionResponseV1: ...


class RetrievalPort(Protocol):
    def execute(self, query: RetrievalQueryV1) -> RetrievalResponseV1: ...


class RetrievalRunPort(Protocol):
    def record(
        self, query: RetrievalQueryV1, response: RetrievalResponseV1
    ) -> RetrievalResponseV1: ...


class RetrievalPreviewPort(Protocol):
    def exchange(
        self,
        *,
        workspace_id: str,
        requester_id: str,
        run_id: str,
        rank: int,
        token: str,
    ) -> RetrievalTemporaryReferenceV1 | None: ...


class McpApplicationPorts(Protocol):
    catalog: CatalogReadPort
    product_briefs: ProductBriefReadPort
    brand_profiles: BrandProfileReadPort
    retrieval: RetrievalPort
    retrieval_runs: RetrievalRunPort
    retrieval_previews: RetrievalPreviewPort
    retrieval_policy_version: str


class CommerceMcpGateway:
    def __init__(
        self,
        ports: McpApplicationPorts,
        *,
        policy_version: str = "mcp-tool-policy-v1",
        maximum_argument_bytes: int = 64 * 1024,
        maximum_output_bytes: int = 256 * 1024,
    ) -> None:
        self._ports = ports
        self._policy_version = policy_version
        self._maximum_output_bytes = maximum_output_bytes
        definitions = self._definitions()
        self._gateway = ToolExecutionGateway(
            registry=ToolRegistry(definitions),
            policy=ToolPolicy(
                version=policy_version,
                allowed_tools=frozenset(item.name for item in definitions),
                max_argument_bytes=maximum_argument_bytes,
            ),
        )

    def definitions(self) -> list[ToolDefinition]:
        return self._gateway.registry.list()

    def execute(
        self,
        name: str,
        arguments: dict[str, object],
        *,
        identity: McpToolIdentityV1,
    ) -> dict[str, object]:
        version = "v1"
        key = stable_tool_key(
            workflow_id=identity.workflow_id,
            step_key=identity.invocation_id,
            tool_name=name,
            tool_version=version,
            policy_version=self._policy_version,
            arguments=arguments,
        )
        context = ToolExecutionContext(
            workflow_id=identity.workflow_id,
            workspace_id=identity.workspace_id,
            actor_id=identity.actor_id,
            trace_id=identity.invocation_id,
            idempotency_key=key,
            policy_version=self._policy_version,
            scopes=frozenset(identity.scopes),
            purpose=identity.purpose,
            provider=identity.provider,
            requires_derivative=identity.requires_derivative,
            maximum_result_count=identity.budget.max_result_count,
            maximum_candidate_count=identity.budget.max_candidate_count,
            maximum_output_bytes=identity.budget.max_output_bytes,
        )
        result = self._gateway.execute(
            context=context,
            invocation=ToolInvocation(
                tool_name=name,
                tool_version=version,
                arguments=arguments,
                idempotency_key=key,
                policy_version=self._policy_version,
                reason="authorized MCP application read",
            ),
        )
        output = result.output
        if (
            output.get("tool_name") != name
            or output.get("tool_version") != version
            or output.get("policy_version") != self._policy_version
        ):
            raise ToolExecutionError("tool output metadata is inconsistent")
        return output

    def _result(self, name: str, key: str, output) -> ToolResult:
        return ToolResult(tool_name=name, tool_version="v1", idempotency_key=key, output=output)

    def _definitions(self) -> list[ToolDefinition]:
        def product(ctx, call):
            request = CatalogGetProductInputV1.model_validate(call.arguments)
            output = CatalogGetProductOutputV1(
                tool_name=call.tool_name,
                tool_version=call.tool_version,
                policy_version=call.policy_version,
                product=self._ports.catalog.get_product(
                    workspace_id=ctx.workspace_id, product_id=request.product_id
                ),
            )
            return self._result(
                call.tool_name, call.idempotency_key, output.model_dump(mode="python")
            )

        def brief(ctx, call):
            request = CatalogGetProductBriefInputV1.model_validate(call.arguments)
            value = self._ports.product_briefs.get(
                workspace_id=ctx.workspace_id, product_brief_id=request.product_brief_id
            )
            version = value.confirmed_version
            if version is None or (
                request.product_brief_version_id is not None
                and request.product_brief_version_id != version.id
            ):
                raise NotFoundError("confirmed ProductBrief Version was not found")
            output = CatalogGetProductBriefOutputV1(
                tool_name=call.tool_name,
                tool_version=call.tool_version,
                policy_version=call.policy_version,
                product_brief_id=value.id,
                confirmation_status="CONFIRMED",
                version=version,
            )
            return self._result(
                call.tool_name, call.idempotency_key, output.model_dump(mode="python")
            )

        def brand(ctx, call):
            request = BrandGetProfileInputV1.model_validate(call.arguments)
            version_number = request.version_number
            if version_number is None:
                head = self._ports.brand_profiles.get(
                    workspace_id=ctx.workspace_id, profile_id=request.profile_id
                )
                version_number = head.current_version_number
            if version_number < 1:
                raise NotFoundError("published Brand Profile Version was not found")
            output = BrandGetProfileOutputV1(
                tool_name=call.tool_name,
                tool_version=call.tool_version,
                policy_version=call.policy_version,
                profile=self._ports.brand_profiles.get_version(
                    workspace_id=ctx.workspace_id,
                    profile_id=request.profile_id,
                    version_number=version_number,
                ),
            )
            return self._result(
                call.tool_name, call.idempotency_key, output.model_dump(mode="python")
            )

        def search(ctx, call):
            request = AssetsSearchInputV1.model_validate(call.arguments)
            if request.top_k > ctx.maximum_result_count:
                raise ToolExecutionError("requested result count exceeds the signed budget")
            query = RetrievalQueryV1(
                workspace_id=ctx.workspace_id,
                requester_id=ctx.actor_id,
                product_id=request.product_id,
                product_brief_id=request.product_brief_id,
                category=request.category,
                brand=request.brand,
                purpose=ctx.purpose,
                provider=ctx.provider,
                requires_derivative=ctx.requires_derivative,
                roles=list(request.roles),
                vector_kinds=list(request.vector_kinds),
                query_text=request.query_text,
                query_image_asset_version_id=request.query_image_asset_version_id,
                explicit_reference_asset_version_ids=list(
                    request.explicit_reference_asset_version_ids
                ),
                brand_profile_id=request.brand_profile_id,
                brand_profile_version=request.brand_profile_version,
                result_limit=request.top_k,
                candidate_limit=ctx.maximum_candidate_count,
                retrieval_policy_version=self._ports.retrieval_policy_version,
            )
            response = self._ports.retrieval_runs.record(
                query, self._ports.retrieval.execute(query)
            )
            output = AssetsSearchOutputV1(
                tool_name=call.tool_name,
                tool_version=call.tool_version,
                policy_version=call.policy_version,
                retrieval=response,
            )
            return self._result(
                call.tool_name, call.idempotency_key, output.model_dump(mode="python")
            )

        def temporary(ctx, call):
            request = AssetsGetTemporaryReferenceInputV1.model_validate(call.arguments)
            reference = self._ports.retrieval_previews.exchange(
                workspace_id=ctx.workspace_id,
                requester_id=ctx.actor_id,
                run_id=request.retrieval_run_id,
                rank=request.rank,
                token=request.preview_reference_token,
            )
            if reference is None:
                raise NotFoundError("Retrieval preview is unavailable")
            output = AssetsGetTemporaryReferenceOutputV1(
                tool_name=call.tool_name,
                tool_version=call.tool_version,
                policy_version=call.policy_version,
                reference=reference,
            )
            return self._result(
                call.tool_name, call.idempotency_key, output.model_dump(mode="python")
            )

        specs = [
            (
                "catalog.get_product.v1",
                "catalog.read",
                CatalogGetProductInputV1,
                CatalogGetProductOutputV1,
                product,
            ),
            (
                "catalog.get_product_brief.v1",
                "catalog.read",
                CatalogGetProductBriefInputV1,
                CatalogGetProductBriefOutputV1,
                brief,
            ),
            (
                "brand.get_profile.v1",
                "brand.read",
                BrandGetProfileInputV1,
                BrandGetProfileOutputV1,
                brand,
            ),
            (
                "assets.search.v1",
                "assets.search",
                AssetsSearchInputV1,
                AssetsSearchOutputV1,
                search,
            ),
            (
                "assets.get_temporary_reference.v1",
                "assets.read",
                AssetsGetTemporaryReferenceInputV1,
                AssetsGetTemporaryReferenceOutputV1,
                temporary,
            ),
        ]
        return [
            ToolDefinition(
                name=name,
                version="v1",
                description=f"CommerceVision {name} controlled application operation.",
                input_schema=input_model.model_json_schema(),
                output_schema=output_model.model_json_schema(),
                implementation=implementation,
                input_model=input_model,
                output_model=output_model,
                required_scopes=frozenset({scope}),
                maximum_output_bytes=self._maximum_output_bytes,
            )
            for name, scope, input_model, output_model, implementation in specs
        ]
