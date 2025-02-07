from ninja import NinjaAPI, Schema, Router, UploadedFile, File
from ninja.errors import HttpError
from django.core.exceptions import ValidationError
from django.db import transaction, models
from typing import List, Any, Dict
from .models import Item, Note, Attachment, Email, ComponentHistory
from functools import reduce
import operator 

api = NinjaAPI()
router = Router()
def validate_parent_relationship(item, parent_id):
    try:
        parent = Item.objects.get(id=parent_id)
    except Item.DoesNotExist:
        raise HttpError(404, "Parent item not found")
    if parent == item or item.is_ancestor_of(parent):
        raise HttpError(400, "Circular dependency detected")
    return parent
# Error response schemas
class ErrorResponse(Schema):
    detail: str

class ValidationErrorResponse(Schema):
    detail: Dict[str, List[str]]

# Base response schemas
class NoteSchema(Schema):
    id: int
    content: str
    created_at: Any

class AttachmentSchema(Schema):
    id: int
    file: str
    type: str
    uploaded_at: Any

class EmailSchema(Schema):
    id: int
    subject: str
    content: str
    created_at: Any

class ComponentHistorySchema(Schema):
    id: int
    item: int | None
    old_parent: int | None
    new_parent: int | None
    changed_at: Any
    item_name: str | None
    old_parent_name: str | None
    new_parent_name: str | None

    @staticmethod
    def resolve_item(obj):
        return obj.item.id if obj.item else None
    
    @staticmethod
    def resolve_old_parent(obj):
        return obj.old_parent.id if obj.old_parent else None

    @staticmethod
    def resolve_new_parent(obj):
        return obj.new_parent.id if obj.new_parent else None

    @staticmethod
    def resolve_item_name(obj):
        return obj.item.name if obj.item else "Deleted Item"

    @staticmethod
    def resolve_old_parent_name(obj):
        return obj.old_parent.name if obj.old_parent else "Storage"

    @staticmethod
    def resolve_new_parent_name(obj):
        return obj.new_parent.name if obj.new_parent else "Storage"
    
# Input/Output schemas for Item
class ItemCreate(Schema):
    name: str
    description: str | None = None
    parent_id: int | None = None

class ItemUpdate(Schema):
    name: str | None = None
    description: str | None = None
    parent_id: int | None = None


# Other input schemas
class NoteCreate(Schema):
    content: str
    author: str = "System"

class EmailCreate(Schema):
    subject: str
    content: str

# Main output schema.
# We add dynamic resolution for children: if the instance has a _children_override attribute (set by flat endpoints)
# we return it; otherwise, use get_children() with prefetch.
class ItemOut(Schema):
    id: int
    name: str
    description: str | None
    created_at: Any
    parent_id: int | None
    level: int
    tree_id: int
    children: List["ItemOut"] = []
    notes: List[NoteSchema] = []
    attachments: List[AttachmentSchema] = []
    emails: List[EmailSchema] = []
    
    @classmethod
    def resolve_children(cls, obj):
        if getattr(obj, '_is_flat_view', False):
            return []
        if hasattr(obj, 'get_children'):
            return list(obj.get_children().prefetch_related('notes', 'attachments', 'emails'))
        return []
    
    @staticmethod
    def resolve_notes(obj):
        return list(obj.notes.all()) if hasattr(obj, 'notes') else []
        
    @staticmethod
    def resolve_attachments(obj):
        return list(obj.attachments.all()) if hasattr(obj, 'attachments') else []
        
    @staticmethod
    def resolve_emails(obj):
        return list(obj.emails.all()) if hasattr(obj, 'emails') else []

# Item endpoints
@router.get('/items', response={200: List[ItemOut], 500: ErrorResponse})
def list_items(request, hierarchical: bool = True):
    try:
        if hierarchical:
            items = Item.objects.root_nodes().prefetch_related(*Item.get_prefetch_fields())
        else:
            # Get all items and force evaluation
            items = Item.objects.all().prefetch_related(*Item.get_prefetch_fields())
            # Set empty children for flat view
            for item in items:
                # This will be picked up by ItemOut.resolve_children
                item.is_flat_view = True
        return items
    except Exception as e:
        raise HttpError(500, str(e))
    
@router.get('/items/{item_id}/tree', response=List[ItemOut])
def get_item_tree(request, item_id: int):
    """Get item and all its descendants"""
    try:
        item = Item.objects.get(id=item_id)
        return item.get_descendants(include_self=True)
    except Item.DoesNotExist:
        raise HttpError(404, "Item not found")

@router.get('/items/{item_id}/breadcrumb', response=List[ItemOut])
def get_breadcrumb(request, item_id: int):
    """Get path from root to item"""
    try:
        item = Item.objects.get(id=item_id)
        return item.get_ancestors(include_self=True)
    except Item.DoesNotExist:
        raise HttpError(404, "Item not found")
    
@router.get('/items/history', response=List[ComponentHistorySchema])
def get_component_history(request):
    return ComponentHistory.objects.all().order_by('-changed_at')

@router.post('/items', response={201: ItemOut, 404: ErrorResponse, 400: ValidationErrorResponse})
def create_item(request, data: ItemCreate):
    try:
        with transaction.atomic():
            if data.parent_id:
                try:
                    parent = Item.objects.get(id=data.parent_id)
                except Item.DoesNotExist:
                    raise HttpError(404, "Parent item not found")
            item = Item(**data.dict())
            item.full_clean()  # Validate before saving
            item.save()
            return 201, item
    except ValidationError as e:
        raise HttpError(400, dict(e))

@router.get('/items/{item_id}', response=ItemOut)
def get_item(request, item_id: int):
    item = Item.objects.filter(id=item_id).select_related('parent').prefetch_related(
        *Item.get_prefetch_fields()
    ).first()
    if not item:
        raise HttpError(404, "Item not found")
    return item

@router.put('/items/{item_id}', response={200: ItemOut, 404: ErrorResponse, 400: ValidationErrorResponse})
def update_item(request, item_id: int, data: ItemUpdate):
    try:
        with transaction.atomic():
            item = Item.objects.get(id=item_id)
            
            if data.parent_id is not None:
                validate_parent_relationship(item, data.parent_id)
            
            for attr, value in data.dict(exclude_unset=True).items():
                setattr(item, attr, value)
                
            item.full_clean()
            item.save()
            return item
    except Item.DoesNotExist:
        raise HttpError(404, "Item not found")
    except ValidationError as e:
        raise HttpError(400, dict(e))

@router.patch('/items/{item_id}', response={200: ItemOut, 404: ErrorResponse, 400: ValidationErrorResponse})
def partial_update_item(request, item_id: int, data: ItemUpdate):
    try:
        with transaction.atomic():
            item = Item.objects.get(id=item_id)
            
            if data.parent_id is not None:
                validate_parent_relationship(item, data.parent_id)
            
            for attr, value in data.dict(exclude_unset=True).items():
                if value is not None:
                    setattr(item, attr, value)
                    
            item.full_clean()
            item.save()
            return item
    except Item.DoesNotExist:
        raise HttpError(404, "Item not found")
    except ValidationError as e:
        raise HttpError(400, dict(e))
@router.get('/items/search', response=List[ItemOut])
def search_items(request, qr_code: str = None, name: str = None, description: str = None):
    print("\nSearch Parameters:")
    print(f"name: {name}, qr_code: {qr_code}, description: {description}")

    filters = []
    if qr_code:
        filters.append(models.Q(qr_code=qr_code))
    if name:
        filters.append(models.Q(name__icontains=name))
    if description:
        filters.append(models.Q(description__icontains=description))

    if filters:
        # Get direct matches
        direct_matches = Item.objects.filter(reduce(operator.or_, filters))
        print("\nDirect Matches:")
        for item in direct_matches:
            print(f"- {item.name} (id:{item.id}, tree_id:{item.tree_id}, lft:{item.lft}, rght:{item.rght})")
        
        # Get all descendants
        all_items = Item.objects.none()
        for item in direct_matches:
            descendant_filter = models.Q(tree_id=item.tree_id, lft__gt=item.lft, rght__lt=item.rght)
            descendants = Item.objects.filter(descendant_filter)
            print(f"\nDescendants for {item.name}:")
            for desc in descendants:
                print(f"- {desc.name} (id:{desc.id}, tree_id:{desc.tree_id}, lft:{desc.lft}, rght:{desc.rght})")
            
            all_items = all_items | Item.objects.filter(
                models.Q(pk=item.pk) | descendant_filter
            )
        
        final_results = all_items.distinct().prefetch_related(*Item.get_prefetch_fields())
        print("\nFinal Results:")
        for item in final_results:
            print(f"- {item.name}")
        
        return final_results
    
    return Item.objects.none()
@router.put('/items/{item_id}/parent', response={200: ItemOut, 404: ErrorResponse, 400: ValidationErrorResponse})
def change_parent(request, item_id: int, data: ItemUpdate):
    """Change item's parent, checking for circular dependencies"""
    try:
        with transaction.atomic():
            item = Item.objects.get(id=item_id)
            old_parent = item.parent
            
            if data.parent_id is not None:
                new_parent = validate_parent_relationship(item, data.parent_id)
            else:
                new_parent = None
            
            item.move_to(new_parent)
            item.refresh_from_db()
            
            ComponentHistory.objects.create(
                item=item,
                old_parent=old_parent,
                new_parent=new_parent
            )
            return item
    except Item.DoesNotExist:
        raise HttpError(404, "Item not found")
# def change_parent(request, item_id: int, data: ItemUpdate):
#     try:
#         with transaction.atomic():
#             item = Item.objects.get(id=item_id)
#             old_parent = item.parent
#             new_parent_id = data.parent_id
#             if new_parent_id is not None:
#                 try:
#                     new_parent = Item.objects.get(id=new_parent_id)
#                 except Item.DoesNotExist:
#                     raise HttpError(404, "Parent item not found")
#                 if item.id == new_parent_id or item.is_ancestor_of(new_parent):
#                     raise HttpError(400, {"message": "Circular dependency detected"})
#             else:
#                 new_parent = None
            
#             # Use MPTT's move_to to maintain tree integrity.
#             item.move_to(new_parent)
#             item.refresh_from_db()

            
#             ComponentHistory.objects.create(
#                 item=item,
#                 old_parent=old_parent,
#                 new_parent=new_parent
#             )
            
#             return item
#     except Item.DoesNotExist:
#         raise HttpError(404, "Item not found")

@router.delete('/items/{item_id}', response={204: None, 404: ErrorResponse})
def delete_item(request, item_id: int):
    try:
        item = Item.objects.get(id=item_id)
        item.delete()
        return 204, None
    except Item.DoesNotExist:
        raise HttpError(404, "Item not found")

# Note endpoints
@router.post('/items/{item_id}/notes', response={201: NoteSchema, 404: ErrorResponse, 400: ValidationErrorResponse})
def create_note(request, item_id: int, data: NoteCreate):
    try:
        with transaction.atomic():
            item = Item.objects.get(id=item_id)
            note = Note(item=item, **data.dict())
            note.full_clean()
            note.save()
            return 201, note
    except Item.DoesNotExist:
        raise HttpError(404, "Item not found")
    except ValidationError as e:
        raise HttpError(400, dict(e))

@router.delete('/items/{item_id}/notes/{note_id}', response={204: None, 404: ErrorResponse})
def delete_note(request, item_id: int, note_id: int):
    try:
        note = Note.objects.get(item_id=item_id, id=note_id)
        note.delete()
        return 204, None
    except Note.DoesNotExist:
        raise HttpError(404, "Note not found")

# Email endpoints
@router.post('/items/{item_id}/emails', response={201: EmailSchema, 404: ErrorResponse, 400: ValidationErrorResponse})
def create_email(request, item_id: int, data: EmailCreate):
    try:
        with transaction.atomic():
            item = Item.objects.get(id=item_id)
            email = Email(item=item, **data.dict())
            email.full_clean()
            email.save()
            return 201, email
    except Item.DoesNotExist:
        raise HttpError(404, "Item not found")
    except ValidationError as e:
        raise HttpError(400, dict(e))

@router.delete('/items/{item_id}/emails/{email_id}', response={204: None, 404: ErrorResponse})
def delete_email(request, item_id: int, email_id: int):
    try:
        email = Email.objects.get(item_id=item_id, id=email_id)
        email.delete()
        return 204, None
    except Email.DoesNotExist:
        raise HttpError(404, "Email not found")

# Attachment endpoints
@router.post('/items/{item_id}/attachments', response={201: AttachmentSchema, 404: ErrorResponse, 400: ValidationErrorResponse})
def create_attachment(request, item_id: int, file: UploadedFile = File(...)):
    try:
        with transaction.atomic():
            item = Item.objects.get(id=item_id)
            
            if not file.content_type.startswith(('image/', 'application/', 'text/')):
                raise ValidationError({'file': ['Unsupported file type']})
            if file.size > 10 * 1024 * 1024:
                raise ValidationError({'file': ['File size exceeds 10MB limit']})
                
            attachment = Attachment(
                item=item,
                file=file,
                type=file.content_type
            )
            attachment.full_clean()
            attachment.save()
            return 201, attachment
    except Item.DoesNotExist:
        raise HttpError(404, "Item not found")
    except ValidationError as e:
        raise HttpError(400, dict(e))

@router.delete('/items/{item_id}/attachments/{attachment_id}', response={204: None, 404: ErrorResponse})
def delete_attachment(request, item_id: int, attachment_id: int):
    try:
        attachment = Attachment.objects.get(item_id=item_id, id=attachment_id)
        attachment.delete()
        return 204, None
    except Attachment.DoesNotExist:
        raise HttpError(404, "Attachment not found")

api.add_router('', router)
