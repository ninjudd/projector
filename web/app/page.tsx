import { Create } from "@/components/sections/create";
import { Example } from "@/components/sections/example";
import { Hero } from "@/components/sections/hero";
import { Install } from "@/components/sections/install";
import { Loop } from "@/components/sections/loop";
import { Panel } from "@/components/sections/panel";
import { Sessions } from "@/components/sections/sessions";
import { Site } from "@/components/sections/site";
import { Tools } from "@/components/sections/tools";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";

export default function Home() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="flex-1">
        <Hero />
        <Loop />
        <Panel />
        <Create />
        <Example />
        <Sessions />
        <Site />
        <Tools />
        <Install />
      </main>
      <SiteFooter />
    </>
  );
}
