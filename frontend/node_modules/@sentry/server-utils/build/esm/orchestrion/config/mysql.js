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

export { mysqlChannels, mysqlConfig };
//# sourceMappingURL=mysql.js.map
