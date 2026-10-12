import toon_format
from toon_out import to_toon, count_line


class TestToToon:
    """Test toon_out.to_toon function"""

    def test_list_of_dicts_roundtrip(self):
        """Test round-trip with list of dicts"""
        rows = [
            {"name": "Alice", "age": 30},
            {"name": "Bob", "age": 25},
        ]
        fields = ["name", "age"]

        toon_str = to_toon(rows, fields)
        decoded = toon_format.loads(toon_str)

        assert decoded == rows

    def test_list_of_dicts_exact_bytes(self):
        """Test exact byte output matches spec format"""
        rows = [
            {"name": "Alice", "age": 30},
            {"name": "Bob", "age": 25},
        ]
        fields = ["name", "age"]

        toon_str = to_toon(rows, fields)
        expected_bytes = b'[2]{name,age}:\n  Alice,30\n  Bob,25'

        assert toon_str.encode() == expected_bytes

    def test_empty_list_roundtrip(self):
        """Test round-trip with empty list"""
        rows = []
        fields = ["name", "age"]

        toon_str = to_toon(rows, fields)
        decoded = toon_format.loads(toon_str)

        assert decoded == []

    def test_empty_list_exact_bytes(self):
        """Test empty list produces correct bytes"""
        rows = []
        fields = ["name"]

        toon_str = to_toon(rows, fields)
        expected_bytes = b'[]'

        assert toon_str.encode() == expected_bytes

    def test_nested_value_roundtrip(self):
        """Test round-trip with nested values"""
        rows = [
            {"id": 1, "data": {"x": 10, "y": 20}},
        ]
        fields = ["id", "data"]

        toon_str = to_toon(rows, fields)
        decoded = toon_format.loads(toon_str)

        assert decoded == rows

    def test_nested_value_exact_bytes(self):
        """Test nested value produces correct bytes"""
        rows = [
            {"data": {"x": 1, "y": 2}},
        ]
        fields = ["data"]

        toon_str = to_toon(rows, fields)
        # Expected format with nested object
        expected_bytes = b'[1]{data{x,y}}:\n  1,2'

        assert toon_str.encode() == expected_bytes

    def test_field_selection(self):
        """Test that only selected fields are included"""
        rows = [
            {"name": "Alice", "age": 30, "city": "NYC"},
        ]
        fields = ["name", "age"]

        toon_str = to_toon(rows, fields)
        decoded = toon_format.loads(toon_str)

        # Should only have name and age, not city
        assert list(decoded[0].keys()) == ["name", "age"]
        assert "city" not in decoded[0]


class TestCountLine:
    """Test count_line function"""

    def test_count_line_format(self):
        """Test count_line produces correct format"""
        result = count_line(5, 10)
        assert result == "count: 5 of 10 total"

    def test_count_line_zero(self):
        """Test count_line with zero"""
        result = count_line(0, 10)
        assert result == "count: 0 of 10 total"

    def test_count_line_complete(self):
        """Test count_line when done"""
        result = count_line(10, 10)
        assert result == "count: 10 of 10 total"
