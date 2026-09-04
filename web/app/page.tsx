import { Agents } from "@/components/sections/agents";
import { Cli } from "@/components/sections/cli";
import { Config } from "@/components/sections/config";
import { Hero } from "@/components/sections/hero";
import { HowItWorks } from "@/components/sections/how-it-works";
import { Install } from "@/components/sections/install";
import { Model } from "@/components/sections/model";
import { Principles } from "@/components/sections/principles";
import { ReviewLoop } from "@/components/sections/review-loop";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";

export default function Home() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="flex-1">
        <Hero />
        <Principles />
        <HowItWorks />
        <Model />
        <Cli />
        <Agents />
        <ReviewLoop />
        <Config />
        <Install />
      </main>
      <SiteFooter />
    </>
  );
}
