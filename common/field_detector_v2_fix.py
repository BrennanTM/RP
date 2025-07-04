# Add this to the existing field_detector.py

def get_field_value(self, record: Dict, field_type: str, default: Any = None) -> Any:
    """
    Get a field value from a record using automatic detection
    Now checks both regular and _v2 versions of fields
    """
    field_name = self.detect_field(field_type, record)
    
    if field_name and field_name in record:
        return record[field_name]
    
    # Check _v2 version
    if field_name:
        v2_field = f"{field_name}_v2"
        if v2_field in record and record[v2_field]:
            return record[v2_field]
    
    # Try variations if it's a complex field name
    if field_name:
        base_name = field_name.split('_')[0]
        for key in record.keys():
            if key.startswith(base_name) and record[key]:
                return record[key]
    
    return default
