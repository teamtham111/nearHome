import Link from "next/link";
import Image from "next/image";

const demos = {
  "1": {
    address: "Blk 214 Bishan Street 23", town: "Bishan", price: "S$838,000", area: "112 sqm · 1,206 sqft", lease: "99-year lease · TOP 1990", flatType: "4A HDB · 3 bedrooms · 2 baths", transit: "Bishan MRT · 1 km / 12 min",
    ownerNote: "We’ve called this home for 11 years and have looked after it carefully. We’re moving closer to family, so we’re ready to pass it on. Open to reasonable offers after viewing.",
    photos: [
      { src: "/demo/listings/bishan-living.jpg", alt: "Bright Bishan flat living room with a broad window and greenery outside" },
      { src: "/demo/listings/bishan-dining.jpg", alt: "Dining area opening into the Bishan flat living room" },
      { src: "/demo/listings/bishan-kitchen.jpg", alt: "Clean, functional kitchen in the Bishan flat" },
    ],
  },
  "2": {
    address: "Blk 11 Holland Drive", town: "Queenstown", price: "S$598,888", area: "65 sqm · 700 sqft", lease: "99-year lease · TOP 1974", flatType: "3I Improved · 2 bedrooms · 1 bath", transit: "Holland Village MRT · 640 m / 8 min",
    ownerNote: "We’ve lived here for 8 years and kept the flat in good, comfortable condition. We’re relocating for work and would love a smooth handover. Price is open to a sensible conversation.",
    photos: [
      { src: "/demo/listings/holland-living.jpg", alt: "Cared-for Holland Drive flat living room with afternoon light" },
      { src: "/demo/listings/holland-dining.jpg", alt: "Dining area in the Holland Drive flat" },
      { src: "/demo/listings/holland-kitchen.jpg", alt: "Older but tidy kitchen in the Holland Drive flat" },
    ],
  },
};

export default async function DemoListingPage({ params }: { params: Promise<{ listing: string }> }) {
  const { listing: id } = await params;
  const flat = demos[id as keyof typeof demos] ?? demos["1"];
  return <main className="nh-demo-listing-page">
    <div className="nh-demo-browser"><span>● ● ●</span><strong>Property listing site</strong><span>Owner-listed · Demo</span></div>
    <article className="nh-demo-listing" data-demo-id="mock-listing">
      <div className="nh-demo-listing-gallery" role="group" aria-label="Illustrative property photos">
        {flat.photos.map((photo, index) => <div className="nh-demo-listing-photo" key={photo.src}><Image src={photo.src} alt={photo.alt} fill priority={index === 0} sizes="(max-width: 767px) 100vw, 50vw" /></div>)}
      </div>
      <div className="nh-demo-listing-details">
        <p className="nh-demo-listing-kicker">HDB RESALE · {flat.town.toUpperCase()}</p>
        <h1>{flat.address}</h1>
        <p className="nh-demo-listing-address">{flat.town}, Singapore</p>
        <strong className="nh-demo-listing-price">{flat.price}</strong>
        <div className="nh-demo-listing-facts"><span>{flat.area} floor area</span><span>{flat.lease}</span><span>{flat.flatType}</span><span>{flat.transit}</span></div>
        <section className="nh-demo-listing-note" aria-labelledby="seller-note-heading">
          <h2 id="seller-note-heading">Note from the owner</h2>
          <p className="nh-demo-listing-copy">“{flat.ownerNote}”</p>
        </section>
        <p className="nh-demo-listing-disclaimer">Fictional listing page for the NearHome walkthrough. Photos and owner note are illustrative.</p>
        <Link href="/session/demo-session" className="nh-demo-return">Return to NearHome to paste the listing →</Link>
      </div>
    </article>
    <p className="nh-demo-listing-hint">Select the page and copy it with the keyboard shortcuts shown in the demo.</p>
  </main>;
}
