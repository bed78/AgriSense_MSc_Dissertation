import { consoleSandbox } from '@sentry/core';
export { getClient, getDynamicSamplingContextFromSpan, shouldPropagateTraceForUrl, withStreamedSpan } from '@sentry/core';
export { S as SEMANTIC_ATTRIBUTE_SENTRY_GRAPHQL_OPERATION, c as SentryPropagator, d as SentrySampler, e as SentrySpanProcessor, f as SentryTracerProvider, g as applyOtelSpanData, h as backfillStreamedSpanDataFromOtel, j as continueTrace, k as enhanceDscWithOpenTelemetryRootSpanName, l as getActiveSpan, m as getRequestSpanData, n as getScopesFromContext, o as getSentryResource, p as getSpanKind, q as getTraceContextForScope, r as isSentryRequestSpan, s as openTelemetrySetupCheck, t as setIsSetup, u as setOpenTelemetryContextAsyncContextStrategy, v as setupEventContextTrace, w as spanHasAttributes, x as spanHasEvents, y as spanHasKind, z as spanHasName, A as spanHasParentId, B as spanHasStatus, C as startInactiveSpan, D as startSpan, E as startSpanManual, F as suppressTracing, G as withActiveSpan, H as wrapClientClass, I as wrapContextManagerClass, J as wrapSamplingDecision } from './asyncContextStrategy-CL7X6mXf.js';

class SentryAsyncLocalStorageContextManager {
  constructor() {
    consoleSandbox(() => {
      console.error("SentryAsyncLocalStorageContextManager is not supported in the browser");
    });
  }
}

export { SentryAsyncLocalStorageContextManager };
//# sourceMappingURL=index.browser.js.map
