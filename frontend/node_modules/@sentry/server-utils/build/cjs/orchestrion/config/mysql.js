Object.defineProperty(exports, Symbol.toStringTag, { value: 'Module' });

const mysqlConfig = [
  {
    channelName: "query",
    module: { name: "mysql", versionRange: ">=2.0.0 <3", filePath: "lib/Connection.js" },
    functionQuery: { expressionName: "query", kind: "Auto" }
  }
];
const mysqlChannels = {
  MYSQL_QUERY: "orchestrion:mysql:query"
};

exports.mysqlChannels = mysqlChannels;
exports.mysqlConfig = mysqlConfig;
//# sourceMappingURL=mysql.js.map
