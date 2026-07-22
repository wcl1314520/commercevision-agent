import type {
  CatalogDeleteRequestV1,
  ErrorResponse,
  ProductCreateRequestV1,
  ProductListResponseV1,
  ProductResponseV1,
  ProductUpdateRequestV1,
  SKUCreateRequestV1,
  SKUResponseV1,
  SKUUpdateRequestV1,
} from "./generated/catalog-api";

export type ProductForm = ProductCreateRequestV1;
export type ProductUpdateForm = ProductUpdateRequestV1;
export type SkuForm = SKUCreateRequestV1;
export type SkuUpdateForm = SKUUpdateRequestV1;

export class CatalogApiError extends Error {
  readonly status: number;
  readonly envelope?: ErrorResponse;

  constructor(status: number, envelope?: ErrorResponse) {
    super(envelope?.message ?? `Catalog request failed with ${status}`);
    this.name = "CatalogApiError";
    this.status = status;
    this.envelope = envelope;
  }
}

function idempotencyKey(action: string): string {
  const random =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `web-${action}-${random}`;
}

export class CatalogApi {
  private readonly baseUrl: string;
  private readonly workspaceId: string;
  private readonly actorId: string;

  constructor({
    baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "",
    workspaceId = "catalog-demo",
    actorId = "catalog-workbench",
  }: {
    baseUrl?: string;
    workspaceId?: string;
    actorId?: string;
  } = {}) {
    this.baseUrl = baseUrl;
    this.workspaceId = workspaceId;
    this.actorId = actorId;
  }

  private async request<T>(
    path: string,
    init: RequestInit = {},
    mutation = false,
  ): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body) {
      headers.set("Content-Type", "application/json");
    }
    headers.set("X-Workspace-Id", this.workspaceId);
    if (mutation) {
      headers.set("X-Actor-Id", this.actorId);
      headers.set("Idempotency-Key", idempotencyKey("catalog"));
    }

    const response = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers,
    });
    if (!response.ok) {
      let envelope: ErrorResponse | undefined;
      try {
        envelope = (await response.json()) as ErrorResponse;
      } catch {
        envelope = undefined;
      }
      throw new CatalogApiError(response.status, envelope);
    }
    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  }

  listProducts(cursor?: string): Promise<ProductListResponseV1> {
    const query = new URLSearchParams({ limit: "50" });
    if (cursor) query.set("cursor", cursor);
    return this.request<ProductListResponseV1>(
      `/api/v1/products?${query.toString()}`,
    );
  }

  getProduct(productId: string): Promise<ProductResponseV1> {
    return this.request<ProductResponseV1>(`/api/v1/products/${productId}`);
  }

  createProduct(payload: ProductForm): Promise<ProductResponseV1> {
    return this.request<ProductResponseV1>(
      "/api/v1/products",
      { method: "POST", body: JSON.stringify(payload) },
      true,
    );
  }

  updateProduct(
    productId: string,
    payload: ProductUpdateForm,
  ): Promise<ProductResponseV1> {
    return this.request<ProductResponseV1>(
      `/api/v1/products/${productId}`,
      { method: "PUT", body: JSON.stringify(payload) },
      true,
    );
  }

  createSku(productId: string, payload: SkuForm): Promise<SKUResponseV1> {
    return this.request<SKUResponseV1>(
      `/api/v1/products/${productId}/skus`,
      { method: "POST", body: JSON.stringify(payload) },
      true,
    );
  }

  updateSku(
    productId: string,
    skuId: string,
    payload: SkuUpdateForm,
  ): Promise<SKUResponseV1> {
    return this.request<SKUResponseV1>(
      `/api/v1/products/${productId}/skus/${skuId}`,
      { method: "PUT", body: JSON.stringify(payload) },
      true,
    );
  }

  deleteProduct(productId: string, payload: CatalogDeleteRequestV1): Promise<void> {
    return this.request<void>(
      `/api/v1/products/${productId}`,
      { method: "DELETE", body: JSON.stringify(payload) },
      true,
    );
  }

  deleteSku(
    productId: string,
    skuId: string,
    payload: CatalogDeleteRequestV1,
  ): Promise<void> {
    return this.request<void>(
      `/api/v1/products/${productId}/skus/${skuId}`,
      { method: "DELETE", body: JSON.stringify(payload) },
      true,
    );
  }
}
