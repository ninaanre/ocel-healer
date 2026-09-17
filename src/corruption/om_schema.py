"""Order Management OCEL schema helpers.

Provides constants and helper functions for accessing Order Management
database table names, event types, object types, and relationship qualifiers
used throughout the corruption injection system.
"""

from __future__ import annotations


def get_om_event_type(om_type: str) -> str:
    """Get the event type name for Order Management database.

    Args:
        om_type: Event type key (e.g., "place order")

    Returns:
        Event type name in OM database
    """
    return om_type


def get_om_event_table(om_table: str) -> str:
    """Get the event table name for Order Management database.

    Args:
        om_table: Event table key (e.g., "event_PlaceOrder")

    Returns:
        Actual table name in OM database
    """
    # For OM database, table names are passed through as-is
    return om_table


def get_om_object_type(om_type: str) -> str:
    """Get the object type name for Order Management database.

    Args:
        om_type: Object type key (e.g., "orders", "products")

    Returns:
        Object type name in OM database (e.g., "Orders", "Products")
    """
    # Map lowercase keys to actual Order Management type names (with capitals)
    OM_TYPE_NAMES = {
        "orders": "Orders",
        "items": "Items",
        "packages": "Packages",
        "customers": "Customers",
        "products": "Products",
        "employees": "Employees",
    }
    return OM_TYPE_NAMES.get(om_type, om_type)


def get_om_object_table(om_table: str) -> str:
    """Get the object table name for Order Management database.

    Args:
        om_table: Object table key (e.g., "object_products")

    Returns:
        Actual table name in OM database (e.g., "object_Products")
    """
    # Map lowercase keys to actual Order Management table names (with capitals)
    OM_TABLE_NAMES = {
        "object_orders": "object_Orders",
        "object_items": "object_Items",
        "object_packages": "object_Packages",
        "object_customers": "object_Customers",
        "object_products": "object_Products",
        "object_employees": "object_Employees",
    }
    return OM_TABLE_NAMES.get(om_table, om_table)


def get_om_e2o_qualifier(om_qual: str) -> str:
    """Get the E2O qualifier name for Order Management database.

    Args:
        om_qual: E2O qualifier key (e.g., "order", "product")

    Returns:
        E2O qualifier name in OM database
    """
    return om_qual


def get_om_o2o_qualifier(om_qual: str) -> str:
    """Get the O2O qualifier name for Order Management database.

    Args:
        om_qual: O2O qualifier key (e.g., "comprises", "processed_by")

    Returns:
        O2O qualifier name in OM database
    """
    return om_qual
