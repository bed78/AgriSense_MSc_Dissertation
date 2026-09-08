import codeTransformer from '@apm-js-collab/code-transformer-bundler-plugins/esbuild';
import { orchestrionTransformOptions } from './options.js';

function sentryOrchestrionPlugin(options = {}) {
  return codeTransformer(orchestrionTransformOptions(options));
}

export { sentryOrchestrionPlugin };
//# sourceMappingURL=esbuild.js.map
