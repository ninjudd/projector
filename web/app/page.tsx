import { Agents } from "@/components/sections/agents";
import { Create } from "@/components/sections/create";
import { Example } from "@/components/sections/example";
import { Hero } from "@/components/sections/hero";
import { Install } from "@/components/sections/install";
import { ReviewLoop } from "@/components/sections/review-loop";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";

export default function Home() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="flex-1">
        <Hero />
        <Create />
        <Example />
        <Agents />
        <ReviewLoop />
        <Install />
      </main>
      <SiteFooter />
    </>
  );
}
