import unittest
import os
import json
import shutil
import tempfile
from unittest.mock import MagicMock, patch

from vault import SecretVault
from sandbox import SandboxExecutor
from api_discovery import APIDiscoveryEngine
from post_dispatcher import PostDispatcher


class TestVault(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.vault = SecretVault(data_dir=self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_store_and_retrieve(self):
        """Store a secret and retrieve it."""
        self.assertTrue(self.vault.store("my_api_key", "sk-secret-12345"))
        result = self.vault.retrieve("my_api_key")
        self.assertEqual(result, "sk-secret-12345")

    def test_retrieve_nonexistent(self):
        """Retrieving a missing key returns None."""
        result = self.vault.retrieve("does_not_exist")
        self.assertIsNone(result)

    def test_list_secrets(self):
        """List returns only secret names, never values."""
        self.vault.store("key_a", "value_a")
        self.vault.store("key_b", "value_b")
        names = self.vault.list_secrets()
        self.assertIn("key_a", names)
        self.assertIn("key_b", names)
        self.assertNotIn("value_a", names)
        self.assertNotIn("value_b", names)

    def test_delete_secret(self):
        """Delete a secret and verify it's gone."""
        self.vault.store("temp_key", "temp_value")
        self.assertTrue(self.vault.delete("temp_key"))
        self.assertIsNone(self.vault.retrieve("temp_key"))
        self.assertNotIn("temp_key", self.vault.list_secrets())


class TestSandbox(unittest.TestCase):
    def setUp(self):
        self.sandbox = SandboxExecutor()

    def test_safe_execution(self):
        """Execute safe code and capture output."""
        result = self.sandbox.execute('print("hello sandbox")')
        self.assertTrue(result["success"])
        self.assertIn("hello sandbox", result["output"])
        self.assertEqual(result["error"], "")

    def test_blocked_import(self):
        """Code with blocked imports is rejected."""
        result = self.sandbox.execute("import os\nprint(os.listdir('.'))")
        self.assertFalse(result["success"])
        self.assertIn("Blocked import", result["error"])

    def test_blocked_import_from(self):
        """from X import Y with blocked module is rejected."""
        result = self.sandbox.execute("from subprocess import run\nrun(['ls'])")
        self.assertFalse(result["success"])
        self.assertIn("Blocked import", result["error"])

    def test_timeout(self):
        """Infinite loop times out."""
        result = self.sandbox.execute("while True: pass", timeout=1)
        self.assertFalse(result["success"])
        self.assertIn("timed out", result["error"])

    def test_injected_globals(self):
        """Verify injected globals are accessible in sandbox."""
        mock_client = MagicMock()
        mock_client.get.return_value = {"status": 200}

        code = 'result = http_client.get("https://api.example.com")\nprint(result)'
        result = self.sandbox.execute(code, injected_globals={"http_client": mock_client})
        self.assertTrue(result["success"])
        mock_client.get.assert_called_once_with("https://api.example.com")

    def test_return_value_capture(self):
        """Verify multiple print statements are captured."""
        code = 'print("line1")\nprint("line2")\nprint(42)'
        result = self.sandbox.execute(code)
        self.assertTrue(result["success"])
        self.assertIn("line1", result["output"])
        self.assertIn("line2", result["output"])
        self.assertIn("42", result["output"])


MOCK_API_DOCS = """
# Social Media API Documentation

API Name: MockSocial API
Base URL: https://api.mocksocial.com/v1

## Endpoints

### Create Post
POST /posts - Create a new social media post
- `content` (string, required)
- `visibility` (string, optional)

### Get Post
GET /posts/{id} - Retrieve a post by ID

### Update Post
PUT /posts/{id} - Update an existing post
- `content` (string, required)

### Delete Post
DELETE /posts/{id} - Delete a post

### List User Posts
GET /users/{user_id}/posts - Get all posts by a user
"""


class TestAPIDiscovery(unittest.TestCase):
    def setUp(self):
        self.engine = APIDiscoveryEngine(orchestrator=None)

    def test_scrape_from_text(self):
        """Passing raw text returns it directly."""
        result = self.engine.scrape_docs(MOCK_API_DOCS)
        self.assertEqual(result, MOCK_API_DOCS)

    def test_generate_manifest_regex(self):
        """Regex fallback extracts endpoints from formatted docs."""
        manifest = self.engine.generate_manifest(MOCK_API_DOCS)

        self.assertEqual(manifest["api_name"], "MockSocial API")
        self.assertEqual(manifest["base_url"], "https://api.mocksocial.com/v1")

        endpoints = manifest["endpoints"]
        self.assertGreaterEqual(len(endpoints), 4)

        # Check POST /posts exists
        post_endpoint = next((e for e in endpoints if e["method"] == "POST" and e["path"] == "/posts"), None)
        self.assertIsNotNone(post_endpoint)
        self.assertIn("Create", post_endpoint["description"])

        # Check GET /posts/{id} has path parameter
        get_endpoint = next((e for e in endpoints if e["method"] == "GET" and "/posts/{id}" in e["path"]), None)
        self.assertIsNotNone(get_endpoint)
        param_names = [p["name"] for p in get_endpoint["parameters"]]
        self.assertIn("id", param_names)

    def test_generate_wrapper_script(self):
        """Generated script contains function definitions for each endpoint."""
        manifest = self.engine.generate_manifest(MOCK_API_DOCS)
        script = self.engine.generate_wrapper_script(manifest)

        self.assertIn("def post_posts(", script)
        self.assertIn("def get_posts_", script)
        self.assertIn("def delete_posts_", script)
        self.assertIn("make_request", script)
        self.assertIn("MockSocial API", script)

    def test_full_pipeline(self):
        """Raw docs -> manifest -> script -> sandbox execution with mock HTTP."""
        doc_text = self.engine.scrape_docs(MOCK_API_DOCS)
        manifest = self.engine.generate_manifest(doc_text)
        script = self.engine.generate_wrapper_script(manifest)

        # Build test code that calls the generated POST function
        mock_client = MagicMock()
        mock_client.request.return_value = {"status": 201, "id": "post_123"}

        test_code = script + '\nresult = post_posts(content="Hello World")\nprint(result)'

        sandbox = SandboxExecutor()
        result = sandbox.execute(test_code, injected_globals={"http_client": mock_client})

        print(f"\nPipeline output: {result['output']}")
        self.assertTrue(result["success"], f"Sandbox error: {result['error']}")

    def test_empty_docs(self):
        """Empty text returns manifest with no endpoints."""
        manifest = self.engine.generate_manifest("")
        self.assertEqual(manifest["endpoints"], [])


class TestPostDispatcher(unittest.TestCase):
    def setUp(self):
        self.dispatcher = PostDispatcher()

    def test_register_and_dispatch_local(self):
        """Register a local handler and dispatch content."""
        handler = MagicMock(return_value="Posted successfully!")
        self.dispatcher.register_platform("twitter", handler=handler, mode="local")

        result = self.dispatcher.dispatch("Hello world", "twitter")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["platform"], "twitter")
        self.assertEqual(result["response"], "Posted successfully!")
        handler.assert_called_once_with("Hello world")

    def test_parse_tags(self):
        """Parse platform tags from prompt text."""
        prompt = "Post this update [twitter:local:post] and also [slack:http:webhook]"
        tags = self.dispatcher.parse_tags(prompt)

        self.assertEqual(len(tags), 2)
        self.assertEqual(tags[0]["platform"], "twitter")
        self.assertEqual(tags[0]["mode"], "local")
        self.assertEqual(tags[0]["target"], "post")
        self.assertEqual(tags[1]["platform"], "slack")
        self.assertEqual(tags[1]["mode"], "http")
        self.assertEqual(tags[1]["target"], "webhook")

    def test_dispatch_http_mode(self):
        """HTTP dispatch calls the registered endpoint."""
        self.dispatcher.register_platform("blog", endpoint="https://blog.example.com/api/post", mode="http")

        with patch("post_dispatcher.urlopen") as mock_urlopen, \
             patch("post_dispatcher.NetworkInterceptor.check_url", return_value=True):
            mock_response = MagicMock()
            mock_response.read.return_value = b'{"ok": true}'
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            result = self.dispatcher.dispatch("My blog post", "blog", mode="http")
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["platform"], "blog")

    def test_dispatch_from_prompt(self):
        """Full flow: tagged prompt parsed and dispatched."""
        handler = MagicMock(return_value="OK")
        self.dispatcher.register_platform("mock", handler=handler, mode="local")

        results = self.dispatcher.dispatch_from_prompt(
            "Share this [mock:local:post]",
            "Test content"
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "success")
        handler.assert_called_once_with("Test content")

    def test_unregistered_platform(self):
        """Dispatching to unknown platform returns error."""
        result = self.dispatcher.dispatch("content", "unknown_platform")
        self.assertEqual(result["status"], "error")
        self.assertIn("not registered", result["response"])


class TestFullPipeline(unittest.TestCase):
    """End-to-end tests combining multiple forge modules."""

    def test_mock_api_to_execution(self):
        """
        Full pipeline: mock API docs -> discover endpoints -> generate script
        -> sandbox execute with mock HTTP -> verify POST request was constructed.
        """
        engine = APIDiscoveryEngine(orchestrator=None)
        sandbox = SandboxExecutor()

        # Step 1: Scrape (raw text)
        doc_text = engine.scrape_docs(MOCK_API_DOCS)

        # Step 2: Generate manifest
        manifest = engine.generate_manifest(doc_text)
        self.assertGreater(len(manifest["endpoints"]), 0)

        # Step 3: Generate wrapper script
        script = engine.generate_wrapper_script(manifest)

        # Step 4: Execute in sandbox with mock HTTP client
        mock_client = MagicMock()
        mock_client.request.return_value = {"status": 201, "id": "new_post_1"}

        test_code = script + '\nresult = post_posts(content="Test post from forge")\nprint(result)'

        result = sandbox.execute(test_code, injected_globals={"http_client": mock_client})
        self.assertTrue(result["success"], f"Execution failed: {result['error']}")

        # Verify the mock HTTP client was called with POST
        mock_client.request.assert_called()
        call_args = mock_client.request.call_args
        self.assertEqual(call_args[0][0], "POST")
        self.assertIn("/posts", call_args[0][1])

    def test_vault_integration_with_dispatch(self):
        """Store API key in vault, dispatch retrieves it for auth header."""
        test_dir = tempfile.mkdtemp()
        try:
            vault = SecretVault(data_dir=test_dir)
            vault.store("blog_api_key", "Bearer-token-xyz")

            dispatcher = PostDispatcher(vault=vault)
            dispatcher.register_platform("blog", endpoint="https://blog.example.com/api", mode="http")

            with patch("post_dispatcher.urlopen") as mock_urlopen, \
                 patch("post_dispatcher.NetworkInterceptor.check_url", return_value=True):
                mock_response = MagicMock()
                mock_response.read.return_value = b'{"posted": true}'
                mock_response.__enter__ = MagicMock(return_value=mock_response)
                mock_response.__exit__ = MagicMock(return_value=False)
                mock_urlopen.return_value = mock_response

                result = dispatcher.dispatch("My post", "blog", mode="http")
                self.assertEqual(result["status"], "success")

                # Verify the request included the auth header from vault
                req_obj = mock_urlopen.call_args[0][0]
                self.assertIn("Authorization", req_obj.headers)
                self.assertEqual(req_obj.headers["Authorization"], "Bearer Bearer-token-xyz")
        finally:
            shutil.rmtree(test_dir, ignore_errors=True)

    def test_end_to_end_forge_flow(self):
        """Discovery + script generation + sandbox execution + local dispatch."""
        engine = APIDiscoveryEngine(orchestrator=None)
        sandbox = SandboxExecutor()
        dispatcher = PostDispatcher()

        dispatched_content = []
        def mock_handler(content):
            dispatched_content.append(content)
            return "Dispatched!"

        dispatcher.register_platform("testplatform", handler=mock_handler, mode="local")

        # Discover
        manifest = engine.generate_manifest(MOCK_API_DOCS)
        self.assertGreater(len(manifest["endpoints"]), 0)

        # Generate & execute script
        script = engine.generate_wrapper_script(manifest)
        mock_client = MagicMock()
        mock_client.request.return_value = {"status": 200}

        exec_result = sandbox.execute(
            script + '\nprint("Script loaded successfully")',
            injected_globals={"http_client": mock_client}
        )
        self.assertTrue(exec_result["success"])

        # Dispatch the generated script as content
        dispatch_result = dispatcher.dispatch(script, "testplatform")
        self.assertEqual(dispatch_result["status"], "success")
        self.assertEqual(len(dispatched_content), 1)
        self.assertIn("post_posts", dispatched_content[0])


if __name__ == "__main__":
    unittest.main()
