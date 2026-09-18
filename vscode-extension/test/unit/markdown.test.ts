import * as assert from "assert";
import { renderMarkdown } from "../../src/ui/markdown";

describe("renderMarkdown", () => {
  it("escapes raw HTML so model output can't inject markup", () => {
    const html = renderMarkdown('<img src=x onerror="alert(1)"><script>alert(2)</script>');
    assert.ok(!html.includes("<img"));
    assert.ok(!html.includes("<script"));
    assert.ok(html.includes("&lt;script&gt;"));
  });

  it("escapes inside code spans and fenced code", () => {
    const html = renderMarkdown("Use `a<b>` then\n\n```python\nif x < 1: print('<hi>')\n```");
    assert.ok(html.includes('<code data-symbol="a&lt;b&gt;">a&lt;b&gt;</code>'));
    assert.ok(html.includes('<pre data-lang="python"><code>if x &lt; 1: print(&#39;&lt;hi&gt;&#39;)</code></pre>'));
  });

  it("renders links as plain text (no navigation from answers)", () => {
    const html = renderMarkdown("See [docs](javascript:alert(1)) now");
    assert.ok(!html.includes("href"));
    assert.ok(html.includes("See docs now"));
  });

  it("renders paragraphs, emphasis, headings and lists", () => {
    const html = renderMarkdown("# Title\n\nSome **bold** and *it*.\n\n- one\n- two\n\n1. a\n2. b");
    assert.ok(html.includes("<h3>Title</h3>"));
    assert.ok(html.includes("<strong>bold</strong>"));
    assert.ok(html.includes("<em>it</em>"));
    assert.ok(html.includes("<ul><li>one</li><li>two</li></ul>"));
    assert.ok(html.includes("<ol><li>a</li><li>b</li></ol>"));
  });

  it("does not treat snake_case or multiplication as emphasis", () => {
    const html = renderMarkdown("call hash_password and 2*3*4");
    assert.ok(!html.includes("<em>"));
  });
});
