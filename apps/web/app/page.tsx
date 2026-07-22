"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import {
  CatalogApi,
  CatalogApiError,
  ProductForm,
  SkuForm,
} from "../lib/catalog-api";
import type {
  ProductResponseV1,
  ProductSummaryResponseV1,
  ProductUpdateRequestV1,
  SKUResponseV1,
  SKUUpdateRequestV1,
} from "../lib/generated/catalog-api";

type LoadState = "loading" | "ready" | "empty" | "error";

const emptyProductForm: ProductForm = {
  source_namespace: "MANUAL",
  external_id: "",
  source_version: "manual-v1",
  title: "",
  category_code: "beauty.skincare",
  brand: "",
  attributes: {},
  expires_at: null,
};

const emptySkuForm: SkuForm = {
  source_namespace: "MANUAL",
  external_id: "",
  source_version: "manual-v1",
  title: "",
  category_code: "beauty.skincare",
  brand: "",
  attributes: {},
  expires_at: null,
};

const api = new CatalogApi();

function messageFor(error: unknown): string {
  if (error instanceof CatalogApiError) {
    if (error.envelope?.code === "VERSION_CONFLICT") {
      return "服务器上的版本已更新，当前表单已刷新，请重新提交。";
    }
    if (error.envelope?.code === "DUPLICATE_EXTERNAL_IDENTIFIER") {
      return "该来源空间中的外部标识已存在。";
    }
    if (error.envelope?.code === "IDEMPOTENCY_CONFLICT") {
      return "重复请求键已用于另一份请求。";
    }
    return error.envelope?.message ?? "目录请求失败，请稍后重试。";
  }
  return "无法连接目录服务，请检查服务状态后重试。";
}

function attributesFromText(value: string): Record<string, unknown> {
  const parsed = JSON.parse(value) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("属性必须是 JSON 对象");
  }
  return parsed as Record<string, unknown>;
}

function attributesText(value: Record<string, unknown> | undefined): string {
  return JSON.stringify(value ?? {}, null, 2);
}

function dateTimeLocalValue(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function awareDateTimeValue(value: string): string | null {
  return value ? new Date(value).toISOString() : null;
}

function isExpired(value: string | null | undefined): boolean {
  return Boolean(value && Date.parse(value) <= Date.now());
}

function productDraft(product: ProductResponseV1): ProductForm {
  return {
    source_namespace: product.source_namespace,
    external_id: product.external_id,
    source_version: product.source_version ?? "manual-v1",
    title: product.title,
    category_code: product.category_code,
    brand: product.brand,
    attributes: product.attributes,
    expires_at: product.expires_at ?? null,
  };
}

function productUpdateRequest(
  value: ProductForm,
  expectedVersion: number,
): ProductUpdateRequestV1 {
  return {
    expected_version: expectedVersion,
    source_version: value.source_version ?? null,
    title: value.title,
    category_code: value.category_code,
    brand: value.brand,
    attributes: value.attributes ?? {},
    expires_at: value.expires_at ?? null,
  };
}

function skuDraft(sku: SKUResponseV1): SkuForm {
  return {
    source_namespace: sku.source_namespace,
    external_id: sku.external_id,
    source_version: sku.source_version ?? "manual-v1",
    title: sku.title,
    category_code: sku.category_code,
    brand: sku.brand,
    attributes: sku.attributes,
    expires_at: sku.expires_at ?? null,
  };
}

function skuUpdateRequest(value: SkuForm, expectedVersion: number): SKUUpdateRequestV1 {
  return {
    expected_version: expectedVersion,
    source_version: value.source_version ?? null,
    title: value.title,
    category_code: value.category_code,
    brand: value.brand,
    attributes: value.attributes ?? {},
    expires_at: value.expires_at ?? null,
  };
}

function ExpiredLabel() {
  return <span className="expired-label">已过期</span>;
}

function ErrorBanner({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="error-banner" role="alert">
      <strong>请求未完成</strong>
      <span>{message}</span>
      {onRetry ? (
        <button className="button button-secondary" onClick={onRetry} type="button">
          重试
        </button>
      ) : null}
    </div>
  );
}

function ProductFields({
  value,
  onChange,
}: {
  value: ProductForm;
  onChange: (next: ProductForm) => void;
}) {
  const update = <K extends keyof ProductForm>(key: K, fieldValue: ProductForm[K]) =>
    onChange({ ...value, [key]: fieldValue });

  return (
    <>
      <div className="form-grid">
        <label>
          <span>来源空间</span>
          <input
            maxLength={64}
            onChange={(event) => update("source_namespace", event.target.value)}
            required
            value={value.source_namespace}
          />
        </label>
        <label>
          <span>外部标识</span>
          <input
            maxLength={128}
            onChange={(event) => update("external_id", event.target.value)}
            required
            value={value.external_id}
          />
        </label>
      </div>
      <label>
        <span>商品名称</span>
        <input
          maxLength={256}
          onChange={(event) => update("title", event.target.value)}
          required
          value={value.title}
        />
      </label>
      <div className="form-grid">
        <label>
          <span>分类编码</span>
          <input
            maxLength={128}
            onChange={(event) => update("category_code", event.target.value)}
            required
            value={value.category_code}
          />
        </label>
        <label>
          <span>品牌</span>
          <input
            maxLength={128}
            onChange={(event) => update("brand", event.target.value)}
            required
            value={value.brand}
          />
        </label>
      </div>
      <label>
        <span>来源版本</span>
        <input
          maxLength={128}
          onChange={(event) => update("source_version", event.target.value)}
          value={value.source_version ?? ""}
        />
      </label>
      <label>
        <span>过期时间（可选）</span>
        <input
          onChange={(event) => update("expires_at", awareDateTimeValue(event.target.value))}
          type="datetime-local"
          value={dateTimeLocalValue(value.expires_at)}
        />
      </label>
      <label>
        <span>结构化属性 JSON</span>
        <textarea
          onChange={(event) => {
            try {
              update("attributes", attributesFromText(event.target.value));
            } catch {
              update("attributes", {});
            }
          }}
          rows={4}
          value={attributesText(value.attributes)}
        />
      </label>
    </>
  );
}

function SkuFields({
  value,
  onChange,
}: {
  value: SkuForm;
  onChange: (next: SkuForm) => void;
}) {
  const update = <K extends keyof SkuForm>(key: K, fieldValue: SkuForm[K]) =>
    onChange({ ...value, [key]: fieldValue });

  return (
    <>
      <div className="form-grid">
        <label>
          <span>SKU 来源空间</span>
          <input
            onChange={(event) => update("source_namespace", event.target.value)}
            required
            value={value.source_namespace}
          />
        </label>
        <label>
          <span>SKU 外部标识</span>
          <input
            onChange={(event) => update("external_id", event.target.value)}
            required
            value={value.external_id}
          />
        </label>
      </div>
      <div className="form-grid">
        <label>
          <span>SKU 名称</span>
          <input
            onChange={(event) => update("title", event.target.value)}
            required
            value={value.title}
          />
        </label>
        <label>
          <span>SKU 品牌</span>
          <input
            onChange={(event) => update("brand", event.target.value)}
            required
            value={value.brand}
          />
        </label>
      </div>
      <label>
        <span>SKU 分类编码</span>
        <input
          onChange={(event) => update("category_code", event.target.value)}
          required
          value={value.category_code}
        />
      </label>
      <label>
        <span>SKU 过期时间（可选）</span>
        <input
          onChange={(event) => update("expires_at", awareDateTimeValue(event.target.value))}
          type="datetime-local"
          value={dateTimeLocalValue(value.expires_at)}
        />
      </label>
      <label>
        <span>SKU 属性 JSON</span>
        <textarea
          onChange={(event) => {
            try {
              update("attributes", attributesFromText(event.target.value));
            } catch {
              update("attributes", {});
            }
          }}
          rows={3}
          value={attributesText(value.attributes)}
        />
      </label>
    </>
  );
}

function ProductList({
  products,
  selectedId,
  onSelect,
}: {
  products: ProductSummaryResponseV1[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="product-list" aria-label="商品列表">
      {products.map((product) => (
        <button
          className={`product-row ${selectedId === product.id ? "is-selected" : ""}`}
          key={product.id}
          onClick={() => onSelect(product.id)}
          type="button"
        >
          <span className="product-row-title">{product.title}</span>
          <span className="product-row-meta">
            {product.brand} · {product.external_id}
          </span>
          <span className="product-row-meta">{product.category_code}</span>
          {isExpired(product.expires_at) ? <ExpiredLabel /> : null}
        </button>
      ))}
    </div>
  );
}

export default function Home() {
  const [products, setProducts] = useState<ProductSummaryResponseV1[]>([]);
  const [listState, setListState] = useState<LoadState>("loading");
  const [listError, setListError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedProduct, setSelectedProduct] = useState<ProductResponseV1 | null>(null);
  const [detailState, setDetailState] = useState<LoadState>("ready");
  const [detailError, setDetailError] = useState<string | null>(null);
  const [productForm, setProductForm] = useState<ProductForm>(emptyProductForm);
  const [skuForm, setSkuForm] = useState<SkuForm>(emptySkuForm);
  const [skuDrafts, setSkuDrafts] = useState<Record<string, SkuForm>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadProducts = useCallback(async () => {
    setListState("loading");
    setListError(null);
    try {
      const response = await api.listProducts();
      setProducts(response.items);
      setListState(response.items.length === 0 ? "empty" : "ready");
      setSelectedId((current) => current ?? response.items[0]?.id ?? null);
    } catch (error) {
      setListState("error");
      setListError(messageFor(error));
    }
  }, []);

  const loadProduct = useCallback(async (productId: string) => {
    setDetailState("loading");
    setDetailError(null);
    try {
      const product = await api.getProduct(productId);
      setSelectedProduct(product);
      setProductForm(productDraft(product));
      setSkuDrafts(
        Object.fromEntries(
          (product.skus ?? []).map((sku) => [sku.id, skuDraft(sku)]),
        ),
      );
      setDetailState("ready");
    } catch (error) {
      setDetailState("error");
      setDetailError(messageFor(error));
    }
  }, []);

  useEffect(() => {
    void loadProducts();
  }, [loadProducts]);

  useEffect(() => {
    if (selectedId) void loadProduct(selectedId);
    else setSelectedProduct(null);
  }, [loadProduct, selectedId]);

  const saveProduct = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedProduct) return;
    setBusy("product");
    setNotice(null);
    try {
      const updated = await api.updateProduct(
        selectedProduct.id,
        productUpdateRequest(productForm, selectedProduct.version),
      );
      setSelectedProduct(updated);
      setProductForm(productDraft(updated));
      setNotice("商品已保存");
      await loadProducts();
    } catch (error) {
      setNotice(messageFor(error));
      if (error instanceof CatalogApiError && error.envelope?.code === "VERSION_CONFLICT") {
        await loadProduct(selectedProduct.id);
      }
    } finally {
      setBusy(null);
    }
  };

  const createProduct = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy("create-product");
    setNotice(null);
    try {
      const created = await api.createProduct(productForm);
      setProductForm(emptyProductForm);
      setNotice("商品已创建");
      await loadProducts();
      setSelectedId(created.id);
    } catch (error) {
      setNotice(messageFor(error));
    } finally {
      setBusy(null);
    }
  };

  const createSku = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedProduct) return;
    setBusy("create-sku");
    setNotice(null);
    try {
      await api.createSku(selectedProduct.id, skuForm);
      setSkuForm(emptySkuForm);
      setNotice("SKU 已创建");
      await loadProduct(selectedProduct.id);
    } catch (error) {
      setNotice(messageFor(error));
    } finally {
      setBusy(null);
    }
  };

  const saveSku = async (sku: SKUResponseV1) => {
    const draft = skuDrafts[sku.id];
    if (!selectedProduct || !draft) return;
    setBusy(`sku-${sku.id}`);
    setNotice(null);
    try {
      await api.updateSku(
        selectedProduct.id,
        sku.id,
        skuUpdateRequest(draft, sku.version),
      );
      setNotice("SKU 已保存");
      await loadProduct(selectedProduct.id);
    } catch (error) {
      setNotice(messageFor(error));
      if (error instanceof CatalogApiError && error.envelope?.code === "VERSION_CONFLICT") {
        await loadProduct(selectedProduct.id);
      }
    } finally {
      setBusy(null);
    }
  };

  const deleteSku = async (sku: SKUResponseV1) => {
    if (!selectedProduct) return;
    setBusy(`delete-sku-${sku.id}`);
    setNotice(null);
    try {
      await api.deleteSku(selectedProduct.id, sku.id, {
        expected_version: sku.version,
      });
      setNotice("SKU 已删除");
      await loadProduct(selectedProduct.id);
    } catch (error) {
      setNotice(messageFor(error));
      if (error instanceof CatalogApiError && error.envelope?.code === "VERSION_CONFLICT") {
        await loadProduct(selectedProduct.id);
      }
    } finally {
      setBusy(null);
    }
  };

  const selectionLabel = useMemo(
    () => selectedProduct?.title ?? "未选择商品",
    [selectedProduct],
  );

  return (
    <div className="workbench">
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>
      <header className="app-header">
        <div>
          <p className="eyebrow">COMMERCEVISION / CATALOG</p>
          <h1>商品目录工作台</h1>
        </div>
        <div className="context-chip">
          <span className="status-dot" aria-hidden="true" />
          <span>工作区：catalog-demo</span>
        </div>
      </header>

      <main className="main-content" id="main-content">
        <section className="workspace-intro" aria-labelledby="workspace-heading">
          <div>
            <p className="eyebrow">PHASE 2 · MANUAL CATALOG</p>
            <h2 id="workspace-heading">商品与 SKU</h2>
            <p className="muted">来源标识与版本由目录服务保存，后续 ERP 接入沿用同一契约。</p>
          </div>
          <div className="intro-metric">
            <strong>{products.length}</strong>
            <span>当前商品</span>
          </div>
        </section>

        {notice ? (
          <div aria-live="polite" className="notice">
            {notice}
          </div>
        ) : null}

        <div className="catalog-layout">
          <aside className="catalog-sidebar" aria-labelledby="products-heading">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">WORKSPACE CATALOG</p>
                <h2 id="products-heading">商品列表</h2>
              </div>
              <button
                className="button button-quiet"
                onClick={() => void loadProducts()}
                type="button"
              >
                刷新
              </button>
            </div>
            {listState === "loading" ? (
              <div className="skeleton-list" aria-label="商品加载中">
                <span />
                <span />
                <span />
              </div>
            ) : null}
            {listState === "error" ? (
              <ErrorBanner message={listError ?? "加载失败"} onRetry={() => void loadProducts()} />
            ) : null}
            {listState === "empty" ? (
              <div className="empty-state">
                <strong>还没有商品</strong>
                <span>从右侧表单创建第一条目录记录。</span>
              </div>
            ) : null}
            {listState === "ready" ? (
              <ProductList
                onSelect={setSelectedId}
                products={products}
                selectedId={selectedId}
              />
            ) : null}
          </aside>

          <section className="catalog-main" aria-labelledby="detail-heading">
            {detailState === "loading" ? (
              <div className="panel loading-panel" aria-label="商品详情加载中">
                <span className="loading-bar wide" />
                <span className="loading-bar" />
                <span className="loading-bar" />
              </div>
            ) : null}
            {detailState === "error" ? (
              <div className="panel">
                <ErrorBanner
                  message={detailError ?? "详情加载失败"}
                  onRetry={() => (selectedId ? void loadProduct(selectedId) : undefined)}
                />
              </div>
            ) : null}
            {detailState === "ready" && selectedProduct ? (
              <>
                <section className="panel" aria-labelledby="detail-heading">
                  <div className="panel-heading">
                    <div>
                      <p className="eyebrow">PRODUCT / {selectedProduct.external_id}</p>
                      <h2 id="detail-heading">{selectionLabel}</h2>
                      {isExpired(selectedProduct.expires_at) ? <ExpiredLabel /> : null}
                    </div>
                    <span className="version-label">版本 {selectedProduct.version}</span>
                  </div>
                  <form className="catalog-form" onSubmit={saveProduct}>
                    <ProductFields onChange={setProductForm} value={productForm} />
                    <div className="form-actions">
                      <button className="button button-primary" disabled={busy === "product"} type="submit">
                        {busy === "product" ? "保存中…" : "保存商品"}
                      </button>
                      <span className="form-hint">保存时会校验当前版本，避免覆盖其他编辑。</span>
                    </div>
                  </form>
                </section>

                <section className="panel" aria-labelledby="sku-heading">
                  <div className="panel-heading">
                    <div>
                      <p className="eyebrow">PRODUCT VARIANTS</p>
                      <h2 id="sku-heading">SKU</h2>
                    </div>
                    <span className="version-label">{selectedProduct.skus?.length ?? 0} 条</span>
                  </div>
                  {selectedProduct.skus?.length ? (
                    <div className="sku-list">
                      {selectedProduct.skus.map((sku) => {
                        const draft = skuDrafts[sku.id] ?? skuDraft(sku);
                        return (
                          <article className="sku-item" key={sku.id}>
                            <div className="sku-meta">
                              <strong>{sku.external_id}</strong>
                              <span>版本 {sku.version} · {sku.category_code}</span>
                              {isExpired(sku.expires_at) ? <ExpiredLabel /> : null}
                            </div>
                            <label>
                              <span>SKU 名称</span>
                              <input
                                onChange={(event) =>
                                  setSkuDrafts({
                                    ...skuDrafts,
                                    [sku.id]: { ...draft, title: event.target.value },
                                  })
                                }
                                value={draft.title}
                              />
                            </label>
                            <label>
                              <span>SKU 属性 JSON</span>
                              <textarea
                                onChange={(event) => {
                                  try {
                                    setSkuDrafts({
                                      ...skuDrafts,
                                      [sku.id]: {
                                        ...draft,
                                        attributes: attributesFromText(event.target.value),
                                      },
                                    });
                                  } catch {
                                    return;
                                  }
                                }}
                                rows={2}
                                value={attributesText(draft.attributes)}
                              />
                            </label>
                            <label>
                              <span>SKU 过期时间（可选）</span>
                              <input
                                onChange={(event) =>
                                  setSkuDrafts({
                                    ...skuDrafts,
                                    [sku.id]: {
                                      ...draft,
                                      expires_at: awareDateTimeValue(event.target.value),
                                    },
                                  })
                                }
                                type="datetime-local"
                                value={dateTimeLocalValue(draft.expires_at)}
                              />
                            </label>
                            <div className="sku-actions">
                              <button
                                className="button button-secondary"
                                disabled={busy === `sku-${sku.id}`}
                                onClick={() => void saveSku(sku)}
                                type="button"
                              >
                                {busy === `sku-${sku.id}` ? "保存中…" : "保存 SKU"}
                              </button>
                              <button
                                className="button button-danger"
                                disabled={busy === `delete-sku-${sku.id}`}
                                onClick={() => void deleteSku(sku)}
                                type="button"
                              >
                                {busy === `delete-sku-${sku.id}` ? "删除中…" : "删除 SKU"}
                              </button>
                            </div>
                          </article>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="empty-state compact">
                      <strong>还没有 SKU</strong>
                      <span>为这个商品添加可管理的规格记录。</span>
                    </div>
                  )}
                  <form className="catalog-form sku-create-form" onSubmit={createSku}>
                    <h3>新增 SKU</h3>
                    <SkuFields onChange={setSkuForm} value={skuForm} />
                    <button className="button button-primary" disabled={busy === "create-sku"} type="submit">
                      {busy === "create-sku" ? "创建中…" : "创建 SKU"}
                    </button>
                  </form>
                </section>
              </>
            ) : null}
            {detailState === "ready" && !selectedProduct ? (
              <section className="panel empty-detail">
                <p className="eyebrow">CREATE PRODUCT</p>
                <h2>创建第一条商品记录</h2>
                <form className="catalog-form" onSubmit={createProduct}>
                  <ProductFields onChange={setProductForm} value={productForm} />
                  <button className="button button-primary" disabled={busy === "create-product"} type="submit">
                    {busy === "create-product" ? "创建中…" : "创建商品"}
                  </button>
                </form>
              </section>
            ) : null}
          </section>
        </div>
      </main>
    </div>
  );
}
