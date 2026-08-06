"""Pydantic request models for the API."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class BrandIn(BaseModel):
    name: str
    is_competitor: bool = False
    is_primary: bool = False
    official_website: Optional[str] = None
    amazon_url: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    logo_url: Optional[str] = None
    monitoring_keywords: list[str] = Field(default_factory=list)
    social_links: dict[str, str] = Field(default_factory=dict)
    ecommerce_links: dict[str, str] = Field(default_factory=dict)


class BrandAnalyzeIn(BaseModel):
    url: str


class ProductIn(BaseModel):
    brand_id: str
    name: str
    sku: Optional[str] = None
    category: Optional[str] = None
    notes: Optional[str] = None


class LinkIn(BaseModel):
    brand_id: str
    product_id: Optional[str] = None
    dimension: str = "sales"
    channel: str = "amazon"
    platform: Optional[str] = None
    url: Optional[str] = None
    label: Optional[str] = None
    region: Optional[str] = None
    source_id: Optional[str] = None
    cadence: str = "daily"
    status: str = "active"
    config: dict[str, Any] = Field(default_factory=dict)


class BrowserCapturedJobIn(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    title: Optional[str] = Field(default=None, max_length=500)
    department: Optional[str] = Field(default=None, max_length=300)
    city: Optional[str] = Field(default=None, max_length=200)
    jd_text: Optional[str] = Field(default=None, max_length=20000)
    posted_at: Optional[str] = Field(default=None, max_length=100)
    refreshed_at: Optional[str] = Field(default=None, max_length=100)
    applicant_signal: Optional[str] = Field(default=None, max_length=500)
    is_open: Optional[bool] = True
    raw: dict[str, Any] = Field(default_factory=dict)


class BrowserHiringCaptureIn(BaseModel):
    brand_id: str
    platform: str = "boss"
    source_url: str = Field(min_length=1, max_length=2000)
    source_title: Optional[str] = Field(default=None, max_length=500)
    page_status: str = "ok"
    page_error: Optional[str] = Field(default=None, max_length=500)
    jobs: list[BrowserCapturedJobIn] = Field(default_factory=list, max_length=200)


class SalesMetricIn(BaseModel):
    brand_id: str
    product_id: Optional[str] = None
    link_id: Optional[str] = None
    snapshot_date: Optional[str] = None
    channel: str = "offline"
    platform: Optional[str] = None
    price: Optional[float] = None
    currency: Optional[str] = "USD"
    review_count: Optional[int] = None
    rating: Optional[float] = None
    rank: Optional[int] = None
    units_est: Optional[int] = None
    revenue_est: Optional[float] = None
    in_stock: Optional[bool] = None
    source: str = "manual"


class RecordIn(BaseModel):
    brand_id: Optional[str] = None
    product_id: Optional[str] = None
    source_id: str = "manual_csv"
    data_type: str = "user_voice"
    dimension: Optional[str] = "voc"
    channel: Optional[str] = None
    platform: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None
    body: Optional[str] = None
    text: Optional[str] = None
    url: Optional[str] = None
    region: Optional[str] = None
    language: Optional[str] = None
    occurred_at: Optional[str] = None


class ImportIn(BaseModel):
    rows: list[dict[str, Any]]
    brand_id: Optional[str] = None
    source_id: str = "manual_csv"


class VocActionIn(BaseModel):
    brand_id: Optional[str] = None
    record_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    owner_team: Optional[str] = None
    priority: str = "medium"
    status: str = "open"
    product: Optional[str] = None
    topic: Optional[str] = None
    due_at: Optional[str] = None


class VocActionUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    owner_team: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    due_at: Optional[str] = None


class WebMonitorIn(BaseModel):
    brand_id: Optional[str] = None
    product_id: Optional[str] = None
    name: Optional[str] = None
    url: str
    scope: str = "single_page"
    crawl_limit: int = 20
    cadence: str = "daily"
    check_interval_minutes: int = Field(default=1440, ge=60, le=43200)
    snapshot_interval_minutes: int = Field(default=1440, ge=60, le=43200)
    capture_now: bool = True


class WebMonitorUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    status: Optional[str] = None
    scope: Optional[str] = None
    crawl_limit: Optional[int] = None
    cadence: Optional[str] = None
    check_interval_minutes: Optional[int] = Field(default=None, ge=60, le=43200)
    snapshot_interval_minutes: Optional[int] = Field(default=None, ge=60, le=43200)


class LinkUpdate(BaseModel):
    url: Optional[str] = None
    label: Optional[str] = None
    status: Optional[str] = None
    platform: Optional[str] = None


class SalesListingUpdate(BaseModel):
    monitor: Optional[bool] = None
    status: Optional[str] = None
    product_id: Optional[str] = None


class LinkedInProfileIn(BaseModel):
    brand_id: str
    profile_url: str
    name: Optional[str] = None
    headline: Optional[str] = None
    title: Optional[str] = None
    notes: Optional[str] = None
    monitor: bool = True


class LinkedInProfileUpdate(BaseModel):
    name: Optional[str] = None
    headline: Optional[str] = None
    title: Optional[str] = None
    notes: Optional[str] = None
    monitor: Optional[bool] = None
    status: Optional[str] = None


class SettingsIn(BaseModel):
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    app_title: Optional[str] = None
    max_tokens: Optional[int] = None
    sellersprite_secret_key: Optional[str] = None
    youtube_api_key: Optional[str] = None
    boss_cookie: Optional[str] = None
    linkedin_cookie: Optional[str] = None


class AiDraftIn(BaseModel):
    keyword: str


class DraftProduct(BaseModel):
    name: str
    category: Optional[str] = None
    sku: Optional[str] = None


class DraftLink(BaseModel):
    dimension: str = "marketing"
    channel: str = "social"
    platform: Optional[str] = None
    url: str
    label: Optional[str] = None


class BrandFromDraftIn(BaseModel):
    name: str
    is_competitor: bool = False
    is_primary: bool = False
    official_website: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    logo_url: Optional[str] = None
    monitoring_keywords: list[str] = Field(default_factory=list)
    social_links: dict[str, str] = Field(default_factory=dict)
    ecommerce_links: dict[str, str] = Field(default_factory=dict)
    products: list[DraftProduct] = Field(default_factory=list)
    links: list[DraftLink] = Field(default_factory=list)
