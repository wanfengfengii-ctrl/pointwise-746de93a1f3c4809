import hashlib
import json
from .product_model import ProductRequest, SummaryRow, SavedProduct, canonical_product_request
from .record_model import require_text


def initialize_product_schema(connection):
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("""CREATE TABLE IF NOT EXISTS products (
            product_id TEXT PRIMARY KEY, request BLOB NOT NULL, content BLOB NOT NULL)""")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


class ProductStore:
    def __init__(self, connection, fault_hook=None):
        self.connection = connection
        self.fault_hook = fault_hook
        initialize_product_schema(connection)

    def _boundary(self, stage):
        if self.fault_hook is not None:
            self.fault_hook(stage)

    def _save(self, request, content):
        request_bytes = canonical_product_request(request)
        product_id = "p-" + hashlib.sha256(
            b"OBS-PRODUCT/1\0" + request_bytes + b"\0" + content).hexdigest()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            old = self.connection.execute("SELECT request,content FROM products WHERE product_id=?",
                                          (product_id,)).fetchone()
            if old is not None:
                if bytes(old[0]) != request_bytes or bytes(old[1]) != content:
                    raise ValueError("immutable product identity collision")
                self.connection.commit()
            else:
                self.connection.execute("INSERT INTO products VALUES(?,?,?)",
                                        (product_id, request_bytes, content))
                self._boundary("after_content")
                self._boundary("before_commit")
                self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return self.get_product(product_id)

    def save_summary(self, request, rows):
        if request.kind != "summary":
            raise ValueError("summary requires summary request")
        rows = tuple(rows)
        SavedProduct("pending", request, rows)
        content = json.dumps([row.as_mapping() for row in rows], ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
        return self._save(request, content)

    def save_export(self, request, data):
        if request.kind != "export" or not isinstance(data, bytes):
            raise ValueError("export requires bytes and export request")
        return self._save(request, data)

    def get_product(self, product_id):
        require_text(product_id, "product_id")
        row = self.connection.execute("SELECT request,content FROM products WHERE product_id=?",
                                      (product_id,)).fetchone()
        if row is None:
            raise ValueError("unknown product: " + product_id)
        encoded_request = json.loads(bytes(row[0]).decode("utf-8"))
        if not isinstance(encoded_request, list) or len(encoded_request) != 6 or encoded_request[0] != 1:
            raise ValueError("invalid stored product request")
        request = ProductRequest(*encoded_request[1:])
        data = bytes(row[1])
        if request.kind == "export":
            return SavedProduct(product_id, request, export_bytes=data)
        values = json.loads(data.decode("utf-8"))
        rows = tuple(SummaryRow(**item) for item in values)
        return SavedProduct(product_id, request, rows)
