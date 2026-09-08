Object.defineProperty(exports, Symbol.toStringTag, { value: 'Module' });

const graphqlConfig = [
  {
    channelName: "parse",
    module: { name: "graphql", versionRange: ">=14.0.0 <17", filePath: "language/parser.js" },
    functionQuery: { functionName: "parse", kind: "Sync" }
  },
  {
    channelName: "validate",
    module: { name: "graphql", versionRange: ">=14.0.0 <17", filePath: "validation/validate.js" },
    functionQuery: { functionName: "validate", kind: "Sync" }
  },
  {
    channelName: "execute",
    module: { name: "graphql", versionRange: ">=14.0.0 <17", filePath: "execution/execute.js" },
    functionQuery: { functionName: "execute", kind: "Auto" }
  }
];
const graphqlChannels = {
  GRAPHQL_PARSE: "orchestrion:graphql:parse",
  GRAPHQL_VALIDATE: "orchestrion:graphql:validate",
  GRAPHQL_EXECUTE: "orchestrion:graphql:execute"
};

exports.graphqlChannels = graphqlChannels;
exports.graphqlConfig = graphqlConfig;
//# sourceMappingURL=graphql.js.map
