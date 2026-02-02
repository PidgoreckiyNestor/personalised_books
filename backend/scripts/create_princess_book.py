#!/usr/bin/env python3
"""
Script to create "Princess! We've been waiting for you" book
1. Uploads manifest and assets to S3
2. Creates database record
"""
import os
import sys
import json
import boto3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.database import SessionLocal
from app.models import Book

SLUG = "princess-waiting-for-you"
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", SLUG)

def get_s3_client():
    return boto3.client(
        's3',
        endpoint_url=settings.AWS_ENDPOINT_URL,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY
    )

def upload_manifest(s3):
    """Upload manifest.json to S3"""
    manifest_path = os.path.join(TEMPLATE_DIR, "manifest.json")
    s3_key = f"templates/{SLUG}/manifest.json"

    s3.upload_file(manifest_path, settings.S3_BUCKET_NAME, s3_key)
    print(f"✅ Uploaded manifest.json → s3://{settings.S3_BUCKET_NAME}/{s3_key}")

def upload_pages(s3):
    """Upload page illustrations to S3"""
    pages_dir = os.path.join(TEMPLATE_DIR, "pages")
    if not os.path.exists(pages_dir):
        print(f"⚠️  No pages directory found at {pages_dir}")
        print("   Create illustrations and put them in: templates/princess-waiting-for-you/pages/")
        return False

    files = [f for f in os.listdir(pages_dir) if f.endswith(('.jpg', '.jpeg', '.png'))]
    if not files:
        print(f"⚠️  No images found in {pages_dir}")
        return False

    for filename in sorted(files):
        local_path = os.path.join(pages_dir, filename)
        s3_key = f"templates/{SLUG}/pages/{filename}"
        s3.upload_file(local_path, settings.S3_BUCKET_NAME, s3_key)
        print(f"✅ Uploaded {filename}")

    return True

def upload_fonts(s3):
    """Upload fonts to S3"""
    fonts_dir = os.path.join(TEMPLATE_DIR, "fonts")
    if not os.path.exists(fonts_dir):
        print(f"⚠️  No fonts directory found at {fonts_dir}")
        return

    for filename in os.listdir(fonts_dir):
        if filename.endswith(('.ttf', '.otf', '.woff', '.woff2')):
            local_path = os.path.join(fonts_dir, filename)
            s3_key = f"templates/{SLUG}/fonts/{filename}"
            s3.upload_file(local_path, settings.S3_BUCKET_NAME, s3_key)
            print(f"✅ Uploaded font: {filename}")

def create_db_record():
    """Create or update book record in database"""
    db = SessionLocal()
    try:
        book = Book(
            slug=SLUG,
            title="Princess! We've been waiting for you",
            subtitle="The adventure continues in Volume 2 of Princess and the Glowing Flower!",
            description="Summoned by the Enchanted Forest itself, the Princess must prove her kind and brave heart to become the true Guardian of the Forest. A beautifully personalized tale of courage, kindness, and believing in your own magic.",
            description_secondary="Features an interactive page where children can design their own magical crown!",
            hero_image=f"templates/{SLUG}/pages/spread_00_cover.jpg",
            gallery_images=[
                f"templates/{SLUG}/pages/spread_02_forest_calls.jpg",
                f"templates/{SLUG}/pages/spread_06_creatures.jpg",
                f"templates/{SLUG}/pages/spread_16_coronation.jpg"
            ],
            bullets=[
                "For kids ages 4-10 years",
                "Preview available before ordering",
                "Encourages bravery and self-confidence",
                "Features an interactive page",
                "Volume 2 of Princess and the Glowing Flower series"
            ],
            age_range="4-10",
            category="girl",
            price_amount=34.99,
            price_currency="USD",
            compare_at_price_amount=44.99,
            discount_percent=22.0,
            specs={
                "idealFor": "Bedtime stories, gifts",
                "ageRange": "4-10 years",
                "characters": "Personalized Princess",
                "genre": "Fantasy Adventure",
                "pages": "36 pages (18 spreads)",
                "shipping": "Print on demand"
            }
        )
        db.merge(book)
        db.commit()
        print(f"✅ Book '{SLUG}' created/updated in database")
    finally:
        db.close()

def list_required_files():
    """Show list of required illustration files"""
    manifest_path = os.path.join(TEMPLATE_DIR, "manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)

    print("\n" + "=" * 60)
    print("📋 REQUIRED ILLUSTRATION FILES")
    print("=" * 60)
    print(f"Location: {TEMPLATE_DIR}/pages/\n")

    for page in manifest["pages"]:
        base_uri = page["base_uri"]
        filename = os.path.basename(base_uri)
        face_swap = "🎭" if page.get("needs_face_swap") else "  "
        interactive = "🎨" if page.get("is_interactive") else "  "
        desc = page.get("description", "")[:40]
        print(f"  {face_swap} {interactive} {filename:<35} {desc}")

    print("\n🎭 = needs face_swap | 🎨 = interactive page")
    print(f"\nResolution: 5102 x 2551 px (2:1 ratio)")

def main():
    print("=" * 60)
    print("📚 Creating book: Princess! We've been waiting for you")
    print("=" * 60)

    s3 = get_s3_client()

    # 1. Upload manifest
    upload_manifest(s3)

    # 2. Upload pages (if exist)
    upload_pages(s3)

    # 3. Upload fonts (if exist)
    upload_fonts(s3)

    # 4. Create DB record
    create_db_record()

    # 5. Show required files
    list_required_files()

    print("\n" + "=" * 60)
    print("✅ DONE!")
    print("=" * 60)

if __name__ == "__main__":
    main()
