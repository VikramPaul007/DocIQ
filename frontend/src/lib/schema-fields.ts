/** Mirrors backend/app/domain/schema_subset.py's field_paths(): a flat
 * {path: type} map for the schema editor's reference panel, so authoring a
 * rule's "field" doesn't require guessing at dotted paths. */

type JsonSchemaNode = {
  type?: string;
  properties?: Record<string, JsonSchemaNode>;
  items?: JsonSchemaNode;
};

export function fieldPaths(schema: unknown): Record<string, string> {
  const paths: Record<string, string> = {};

  function walk(node: JsonSchemaNode | undefined, path: string): void {
    if (!node || typeof node !== "object") return;
    if (node.type === "object") {
      for (const [name, child] of Object.entries(node.properties ?? {})) {
        walk(child, path ? `${path}.${name}` : name);
      }
    } else if (node.type === "array") {
      walk(node.items, `${path}[]`);
    } else if (node.type) {
      paths[path] = node.type;
    }
  }

  walk(schema as JsonSchemaNode, "");
  return paths;
}
