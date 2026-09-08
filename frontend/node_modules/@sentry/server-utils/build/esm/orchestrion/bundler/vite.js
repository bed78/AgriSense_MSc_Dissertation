import codeTransformer from '@apm-js-collab/code-transformer-bundler-plugins/vite';
import { instrumentedModuleNames } from '../config/index.js';
import { orchestrionTransformOptions } from './options.js';

function sentryOrchestrionPlugin(options = {}) {
  return {
    ...codeTransformer(orchestrionTransformOptions(options)),
    config() {
      return { ssr: { noExternal: instrumentedModuleNames(options.instrumentations) } };
    }
  };
}

export { sentryOrchestrionPlugin };
//# sourceMappingURL=vite.js.map
